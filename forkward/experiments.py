"""Control experiments, each runnable with one command and a model key. Train and validation only.

compute-matched  A seed team (k-1 personas plus a classifier, so k model calls per hunk) against
                 its best single persona with k samples and a self-consistency vote, using the
                 same operators, rule tuning and keep-vendor prior. Each arm has its own Runner,
                 so neither reuses the other's calls, and each arm's wall-clock is recorded.
no-reflector     A reflection child (reflector plus curator) against two children at the same
                 per-hunk budget: the parent playbook with fresh samples, and a placebo playbook
                 of generic lessons with as many lessons as the reflection child has.
Every arm is reported raw and with a keep-vendor fallback, once where the arm gave no answer and
once where its gate abstained, so each arm's gain over the keep-vendor prior is visible.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from forkward import gate, llm, score, stats
from forkward.agents import EMPTY_PLAYBOOK, Lesson, Playbook, lesson_id, playbook_id
from forkward.data import Hunk, halves, load_hunks
from forkward.search import (
    DEFAULT_RULE,
    SEED_TEAMS,
    Outcome,
    Runner,
    Strategy,
    calibrate,
    env_int,
    make_child,
    measure,
    new_id,
    per_hunk,
    reselect,
    subset,
    summary,
    tune_rule,
)
from forkward.store import Store

PLACEBO_LESSONS = (
    "Read the whole conflict block before deciding on a resolution.",
    "Keep the indentation of the surrounding lines in the answer.",
    "Write only the lines that replace the conflict block, with no commentary.",
    "Consider both sides of the conflict before writing the answer.",
    "Follow the requested answer format exactly.",
    "Look at the lines just above and below the conflict for context.",
    "Do not put explanations inside the resolution block.",
    "Prefer an answer that a reviewer can check quickly.",
    "Take care with blank lines at the start and end of the block.",
    "Check the answer once more before replying.",
    "Answer every conflict on its own merits.",
    "Make the answer complete rather than partial.",
)


def placebo_playbook(n: int, parent: str = "pb-0") -> Playbook:
    """Generic lessons with no information about merges, as many as `n`."""
    texts = [PLACEBO_LESSONS[i % len(PLACEBO_LESSONS)] for i in range(n)]
    lessons = [Lesson(lesson_id(f"{t} {i}"), t) for i, t in enumerate(texts)]
    return Playbook(playbook_id(f"placebo:{parent}", lessons), parent, lessons)


def fallback(split: str, outs: dict[str, Outcome], s: Strategy, threshold: float, hunks) -> dict:
    """Raw accuracy, and accuracy when keep vendor replaces no answer or a gate abstention."""
    key = score.load_key(split)
    vendor = {h.tag: h.vendor for h in hunks}
    tags = [t for t in key if t in outs]

    def acc(pick) -> dict[str, float]:
        return {t: float(score.is_correct(pick(outs[t]), key[t])) for t in tags}

    raw = acc(lambda o: o.decision.lines)
    # An empty resolution is a real answer (delete the block), so only None falls back.
    on_none = {
        t: float(
            score.is_correct(
                vendor[t] if outs[t].decision.lines is None else outs[t].decision.lines, key[t]
            )
        )
        for t in tags
    }
    on_abstain = {
        t: float(
            score.is_correct(
                outs[t].decision.lines
                if outs[t].decision.accepted(threshold, s.use_classifier)
                else vendor[t],
                key[t],
            )
        )
        for t in tags
    }
    n = max(1, len(tags))
    return {
        "threshold": threshold,
        "raw": sum(raw.values()) / n,
        "fallback_no_answer": sum(on_none.values()) / n,
        "fallback_abstain": sum(on_abstain.values()) / n,
        "per_hunk": {"raw": raw, "fallback_no_answer": on_none, "fallback_abstain": on_abstain},
    }


async def run_arm(
    runner: Runner, s: Strategy, train: list[Hunk], val: list[Hunk], cal_half, tune: bool = True
) -> dict:
    """Run a strategy on train, tune its selection rule there, run it on validation, calibrate
    its gate on the calibration half, and time each split."""
    t0 = time.monotonic()
    tr = await runner.run(s, train, "train", 0)
    t_train = time.monotonic() - t0
    if tune:
        s.rule, _ = tune_rule(s, tr)
        tr = reselect(tr, s)
    t1 = time.monotonic()
    va = reselect(await runner.run(s, val, "validation", 0), s)
    t_val = time.monotonic() - t1
    rec = arm_record(
        runner, s, tr, va, cal_half, train, val, {"train": t_train, "validation": t_val}
    )
    rec["_train_outs"] = tr
    return rec


def arm_record(runner, s, tr, va, cal_half, train, val, wall) -> dict:
    cal = calibrate("validation", subset(va, cal_half), s)
    thr = gate.usable_threshold(cal)
    return {
        "strategy": s.doc(),
        "calls": runner.calls.get(s.id, 0),
        "calls_per_hunk": runner.calls.get(s.id, 0) / max(1, len(train) + len(val)),
        "tokens": runner.tokens.get(s.id, 0),
        "wall_clock_s": {k: round(v, 2) for k, v in wall.items()},
        "calibration": {k: cal[k] for k in ("threshold", "target_met", "bound", "accepted")},
        "train": {
            **summary(measure("train", tr, s)),
            "fallback": fallback("train", tr, s, thr, train),
        },
        "validation": {
            **summary(measure("validation", va, s, thr)),
            "fallback": fallback("validation", va, s, thr, val),
        },
        "_per_hunk": {"train": per_hunk("train", tr), "validation": per_hunk("validation", va)},
    }


def comparisons(arms: dict[str, dict], pairs: list[tuple[str, str]]) -> dict:
    """Exact McNemar and bootstrap intervals for each (a, b) pair, raw and with fallback, on each
    split, Holm-adjusted across the whole family."""
    out = {}
    for split in ("train", "validation"):
        cl = stats.clusters_for(split)
        for a, b in pairs:
            out[f"{split}: {b} vs {a}"] = stats.compare(
                arms[a]["_per_hunk"][split], arms[b]["_per_hunk"][split], cl
            )
            fa = arms[a][split]["fallback"]["per_hunk"]["fallback_no_answer"]
            fb = arms[b][split]["fallback"]["per_hunk"]["fallback_no_answer"]
            out[f"{split}: {b} vs {a}, keep-vendor fallback"] = stats.compare(fa, fb, cl)
    return stats.adjust(out)


def _splits() -> tuple[list[Hunk], list[Hunk], frozenset]:
    tk, vk = score.load_key("train"), score.load_key("validation")
    train = [h for h in load_hunks("train") if h.tag in tk]
    val = [h for h in load_hunks("validation") if h.tag in vk]
    _, cal_half = halves("validation", env_int("VALIDATION_SPLIT_SEED", 0))
    return train, val, cal_half


def best_solo(team_outs: dict[str, Outcome], personas: list[str]) -> str:
    """The team persona whose own answers were most accurate on train (train labels only)."""
    key = score.load_key("train")

    def acc(p: str) -> int:
        return sum(
            score.is_correct(next(x.lines for x in o.proposals if x.persona == p), key[t])
            for t, o in team_outs.items()
            if t in key
        )

    return max(personas, key=acc)


async def compute_matched(team_index: int = 0, run_id: str | None = None) -> dict:
    llm.load_env()
    llm.BENCHMARK = True
    store = Store(run_id or f"exp-compute-matched-{time.strftime('%Y%m%dT%H%M%S')}")
    train, val, cal_half = _splits()
    personas = SEED_TEAMS[team_index]
    team = Strategy(
        new_id("s", personas, "pb-0", DEFAULT_RULE, "team"),
        list(personas),
        "pb-0",
        dict(DEFAULT_RULE),
        True,
    )
    pb = {EMPTY_PLAYBOOK.version: EMPTY_PLAYBOOK}
    team_runner = Runner(store, dict(pb))
    team_arm = await run_arm(team_runner, team, train, val, cal_half)
    # One call per persona plus the classifier call.
    k = len(personas) + 1
    solo = best_solo(team_arm["_train_outs"], list(personas))
    rule = {**DEFAULT_RULE, "w_cls": 0.0, "samples": k}
    single = Strategy(new_id("s", [solo], "pb-0", rule, "single"), [solo], "pb-0", rule, False)
    single_runner = Runner(store, dict(pb))
    single_arm = await run_arm(single_runner, single, train, val, cal_half)
    arms = {"team": team_arm, "single": single_arm}
    res = {
        "experiment": "compute-matched",
        "team": list(personas),
        "single_persona": solo,
        "samples": k,
        "arms": _public(arms),
        "comparisons": comparisons(arms, [("single", "team")]),
    }
    return _finish(store, res)


async def no_reflector(team_index: int = 0, run_id: str | None = None) -> dict:
    llm.load_env()
    llm.BENCHMARK = True
    store = Store(run_id or f"exp-no-reflector-{time.strftime('%Y%m%dT%H%M%S')}")
    train, val, cal_half = _splits()
    by_tag = {h.tag: h for h in train}
    personas = list(SEED_TEAMS[team_index])
    parent = Strategy(
        new_id("s", personas, "pb-0", DEFAULT_RULE, "parent"),
        personas,
        "pb-0",
        dict(DEFAULT_RULE),
        True,
    )
    runner = Runner(store, {EMPTY_PLAYBOOK.version: EMPTY_PLAYBOOK})
    p_train = await runner.run(parent, train, "train", 0)
    parent.rule, _ = tune_rule(parent, p_train)
    p_train = reselect(p_train, parent)
    cap = env_int("PLAYBOOK_MAX_LESSONS", 12)
    t0 = time.monotonic()
    made = await make_child(parent, 1, p_train, by_tag, train, runner, store, cap)
    if made is None:
        raise RuntimeError("the parent made no training errors, so there is nothing to reflect on")
    child, c_train = made
    t_train = time.monotonic() - t0
    t1 = time.monotonic()
    c_val = reselect(await runner.run(child, val, "validation", 1), child)
    reflect = arm_record(
        runner, child, c_train, c_val, cal_half, train, val,
        {"train": t_train, "validation": time.monotonic() - t1},
    )  # fmt: skip
    reflect["extra_calls"] = {"reflector": 1, "curator": 1}
    n_lessons = len(runner.playbooks[child.playbook].lessons)
    placebo = placebo_playbook(n_lessons)
    runner.playbooks[placebo.version] = placebo
    store.add("playbook_versions", {**placebo.to_doc(), "round": 1, "placebo": True})
    pl = Strategy(
        new_id("s", personas, placebo.version, DEFAULT_RULE, "placebo"),
        personas,
        placebo.version,
        dict(DEFAULT_RULE),
        True,
        parent.id,
        1,
    )
    placebo_arm = await run_arm(runner, pl, train, val, cal_half)
    fresh = Runner(store, {EMPTY_PLAYBOOK.version: EMPTY_PLAYBOOK}, seed=runner.seed + 1)
    rs = Strategy(
        new_id("s", personas, "pb-0", DEFAULT_RULE, "resample"),
        personas,
        "pb-0",
        dict(DEFAULT_RULE),
        True,
        parent.id,
        1,
    )
    resample_arm = await run_arm(fresh, rs, train, val, cal_half)
    arms = {"reflect": reflect, "placebo": placebo_arm, "resample": resample_arm}
    res = {
        "experiment": "no-reflector",
        "team": personas,
        "lessons": n_lessons,
        "lesson_chars": {
            "reflect": sum(len(x.text) for x in runner.playbooks[child.playbook].lessons),
            "placebo": sum(len(x.text) for x in placebo.lessons),
        },
        "arms": _public(arms),
        "comparisons": comparisons(arms, [("placebo", "reflect"), ("resample", "reflect")]),
    }
    return _finish(store, res)


def _public(arms: dict[str, dict]) -> dict:
    return {
        name: {k: v for k, v in a.items() if not k.startswith("_")} | {"per_hunk": a["_per_hunk"]}
        for name, a in arms.items()
    }


def _finish(store: Store, res: dict) -> dict:
    res["run_id"] = store.run_id
    (store.dir / "experiment.json").write_text(json.dumps(res, indent=1, default=str))
    out = Path("results") / f"experiment-{res['experiment']}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(res, indent=1, default=str))
    store.add("evals", {"kind": "experiment", "experiment": res["experiment"]})
    store.close()
    return res


def render(res: dict) -> str:
    lines = [f"{res['experiment']} (run {res['run_id']})"]
    for name, a in res["arms"].items():
        for split in ("train", "validation"):
            m, f = a[split], a[split]["fallback"]
            lines.append(
                f"  {name:<9} {split:<10} {m['correct']}/{m['n']} raw {f['raw']:.1%}, "
                f"fallback on no answer {f['fallback_no_answer']:.1%}, on abstain "
                f"{f['fallback_abstain']:.1%}"
            )
        lines.append(
            f"  {name:<9} calls {a['calls']} ({a['calls_per_hunk']:.2f} per hunk), tokens "
            f"{a['tokens']}, wall-clock {a['wall_clock_s']}"
        )
    for k, c in res["comparisons"].items():
        lines.append("  " + stats.render(k, c, 44))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="forkward experiment")
    p.add_argument("name", choices=("compute-matched", "no-reflector"))
    p.add_argument("--team", type=int, default=0, help="index into SEED_TEAMS")
    p.add_argument("--run-id", default=None)
    a = p.parse_args(argv)
    fn = compute_matched if a.name == "compute-matched" else no_reflector

    async def go() -> dict:
        try:
            return await fn(a.team, a.run_id)
        finally:
            await llm.close()

    print(render(asyncio.run(go())))
    return 0
