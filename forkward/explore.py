"""Continue learning from a frozen strategy with UCB-guided tree search over strategy configurations.

Each node is a strategy (personas, playbook version, selection rule, harness flags). Each round the
controller expands the strongest nodes; which expansion to apply is chosen by UCB1 over four arms,
rewarded by the child's train accuracy gain over its parent (line-F1 gain breaks ties):

  reflect      reflector + curator write a child playbook from scored failures
  retrieval    resolvers see how the most similar training conflicts were resolved (labels only)
  samples      two samples per persona, the second at higher temperature (self-consistency)
  judge        a pairwise judge breaks close votes between the top two candidates

The best child by train accuracy challenges the incumbent on the selection half of validation and
is promoted only when a one-sided exact McNemar test passes at PROMOTION_ALPHA spent over the
planned rounds, the same rule as the original search. The gate is calibrated on the other half.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import asdict
from pathlib import Path

from forkward import llm, score
from forkward.agents import EMPTY_PLAYBOOK, Playbook
from forkward.data import Hunk, halves, load_hunks
from forkward.search import (
    Outcome,
    Runner,
    Strategy,
    calibrate,
    env_int,
    make_child,
    measure,
    new_id,
    pareto,
    promote,
    promotion_alpha,
    reselect,
    subset,
    summary,
    tune_rule,
)
from forkward.store import RUNS, Store

ARMS = ("reflect", "retrieval", "samples", "judge")
UCB_C = 0.1


def applicable(s: Strategy, arm: str) -> bool:
    if arm == "retrieval":
        return not s.rule.get("retrieval")
    if arm == "samples":
        return int(s.rule.get("samples", 1)) < 2
    if arm == "judge":
        return not s.rule.get("judge")
    return True


def ucb_pick(stats: dict, parent: Strategy, taken: set) -> str | None:
    arms = [a for a in ARMS if applicable(parent, a) and (parent.id, a) not in taken]
    if not arms:
        return None
    untried = [a for a in arms if stats[a]["n"] == 0]
    if untried:
        return untried[0]
    total = sum(v["n"] for v in stats.values())

    def ucb(a: str) -> float:
        v = stats[a]
        return v["reward"] / v["n"] + UCB_C * math.sqrt(2 * math.log(total) / v["n"])

    return max(arms, key=ucb)


async def expand(
    parent: Strategy,
    arm: str,
    r: int,
    outs: dict[str, Outcome],
    by_tag: dict[str, Hunk],
    train: list[Hunk],
    runner: Runner,
    store: Store,
    cap: int,
) -> tuple[Strategy, dict[str, Outcome]] | None:
    if arm == "reflect":
        return await make_child(parent, r, outs, by_tag, train, runner, store, cap)
    flag = {"retrieval": {"retrieval": True}, "samples": {"samples": 2}, "judge": {"judge": True}}[
        arm
    ]
    rule = {**parent.rule, **flag}
    child = Strategy(
        new_id("s", parent.personas, parent.playbook, rule, parent.id, r, arm),
        parent.personas,
        parent.playbook,
        rule,
        True,
        parent.id,
        r,
    )
    store.event("controller", "start", task=f"expand {parent.id} with {arm}", round=r)
    child_outs = await runner.run(child, train, "train", r)
    child.rule, m = tune_rule(child, child_outs)
    child_outs = reselect(child_outs, child)
    child.train = summary(m)
    store.add("strategies", {**child.doc(), "arm": arm})
    return child, child_outs


async def explore(
    warm: str, rounds: int, run_id: str | None = None, ucb_from: str | None = None
) -> Path:
    llm.load_env()
    llm.BENCHMARK = True
    store = Store(run_id)
    if not llm.reportable():
        store.event("controller", "alert", level="warn", message="dev provider: not reportable")
    final = json.loads((RUNS / warm / "final.json").read_text())
    fields = Strategy.__dataclass_fields__
    root = Strategy(**{k: v for k, v in final["strategy"].items() if k in fields})
    baseline = Strategy(**{k: v for k, v in final["baseline"].items() if k in fields})
    pb = Playbook.from_doc(final["playbook"])
    playbooks = {EMPTY_PLAYBOOK.version: EMPTY_PLAYBOOK, pb.version: pb}
    for pv in (RUNS / warm / "playbook_versions.jsonl").read_text().splitlines():
        d = json.loads(pv)
        playbooks.setdefault(d["version"], Playbook.from_doc(d))
    store.add("playbook_versions", {**pb.to_doc(), "round": 0, "warm_start": warm})
    runner = Runner(store, playbooks)
    reused = runner.preload(RUNS / warm)
    store.event(
        "controller", "start", task=f"warm start from {warm}: {reused} logged proposals reused"
    )

    cap = env_int("PLAYBOOK_MAX_LESSONS", 12)
    children_per_round = env_int("CHILDREN_PER_ROUND", 2)
    pop_size = env_int("POPULATION_SIZE", 6)
    train_key, val_key = score.load_key("train"), score.load_key("validation")
    train = [h for h in load_hunks("train") if h.tag in train_key]
    val = [h for h in load_hunks("validation") if h.tag in val_key]
    by_tag = {h.tag: h for h in train}
    split_seed = env_int("VALIDATION_SPLIT_SEED", 0)
    sel_half, cal_half = halves("validation", split_seed)
    alpha = promotion_alpha(rounds)

    root_train, inc_val, b_val = await asyncio.gather(
        runner.run(root, train, "train", 0),
        runner.run(root, val, "validation", 0),
        runner.run(baseline, val, "validation", 0),
    )
    root_train = reselect(root_train, root)
    root.train = summary(measure("train", root_train, root))
    root.validation = summary(measure("validation", inc_val, root))
    baseline.validation = summary(measure("validation", b_val, baseline))
    incumbent = root
    train_outs = {root.id: root_train}
    population = [root]
    stats = {a: {"n": 0, "reward": 0.0} for a in ARMS}
    if ucb_from:
        # Carry measured arm rewards forward so this run exploits what earlier rounds learned.
        prior = json.loads((RUNS / ucb_from / "final.json").read_text()).get("ucb", {})
        for a, v in prior.items():
            if a in stats:
                stats[a] = {"n": int(v["n"]), "reward": float(v["reward"])}
    kv = {
        "train": score.evaluate(train_key, {h.tag: h.vendor for h in train})["accuracy"],
        "validation": score.evaluate(val_key, {h.tag: h.vendor for h in val})["accuracy"],
    }
    store.add(
        "rounds",
        {
            "round": 0,
            "warm_start": warm,
            "best_train": root.train,
            "incumbent": root.id,
            "incumbent_validation": root.validation,
            "baseline": {"validation": baseline.validation},
            "keep_vendor": kv,
            "population": [root.id],
            "ucb": {a: dict(v) for a, v in stats.items()},
            "ucb_from": ucb_from,
        },
    )
    store.flush()

    for r in range(1, rounds + 1):
        taken: set = set()
        plan = []
        for parent in population[:children_per_round]:
            arm = ucb_pick(stats, parent, taken)
            if arm:
                taken.add((parent.id, arm))
                plan.append((parent, arm))
        store.event(
            "controller",
            "start",
            task="round plan: " + ", ".join(f"{p.id} + {a}" for p, a in plan),
            round=r,
        )
        results = await asyncio.gather(
            *(expand(p, a, r, train_outs[p.id], by_tag, train, runner, store, cap) for p, a in plan)
        )
        expansions = []
        kids = []
        for (parent, arm), res in zip(plan, results, strict=True):
            if res is None:
                continue
            child, outs = res
            train_outs[child.id] = outs
            reward = (child.train["accuracy"] - parent.train["accuracy"]) + 0.1 * (
                child.train.get("f1", 0) - parent.train.get("f1", 0)
            )
            stats[arm]["n"] += 1
            stats[arm]["reward"] += reward
            expansions.append(
                {
                    "parent": parent.id,
                    "arm": arm,
                    "child": child.id,
                    "parent_train": parent.train["accuracy"],
                    "child_train": child.train["accuracy"],
                    "reward": round(reward, 4),
                }
            )
            kids.append(child)
        population = pareto(population + kids, pop_size)
        decision = {"promoted": False, "reason": "no child"}
        if kids:
            challenger = max(kids, key=lambda s: (s.train["accuracy"], s.train.get("f1", 0)))
            ch_val = await runner.run(challenger, val, "validation", r)
            challenger.validation = summary(measure("validation", ch_val, challenger))
            decision = promote(
                "accuracy", incumbent, inc_val, challenger, ch_val, alpha, sel_half, cal_half
            )
            store.add("strategies", {**challenger.doc(), "promotion": decision})
            if decision["promoted"]:
                incumbent, inc_val = challenger, ch_val
            store.event(
                "controller",
                "alert",
                level="promotion" if decision["promoted"] else "info",
                message=f"round {r}: {challenger.id} ("
                + "+".join(e["arm"] for e in expansions if e["child"] == challenger.id)
                + ") "
                + ("promoted" if decision["promoted"] else "rejected")
                + f" ({decision['reason']})",
                round=r,
            )
        store.add(
            "rounds",
            {
                "round": r,
                "best_train": population[0].train,
                "best_train_id": population[0].id,
                "incumbent": incumbent.id,
                "incumbent_validation": incumbent.validation,
                "promotion": decision,
                "expansions": expansions,
                "ucb": {a: dict(v) for a, v in stats.items()},
                "population": [s.id for s in population],
                "keep_vendor": kv,
            },
        )
        store.flush()

    cal = calibrate("validation", subset(inc_val, cal_half), incumbent)
    cal["half"] = {"seed": split_seed, "calibration": len(cal_half), "selection": len(sel_half)}
    incumbent.validation = summary(measure("validation", inc_val, incumbent, cal["threshold"]))
    incumbent.validation["calibration"] = cal
    out = {
        "run_id": store.run_id,
        "warm_start": warm,
        "model": llm.model_for("generator"),
        "thinking": llm.thinking(),
        "provider": llm.provider()[0],
        "strategy": incumbent.doc(),
        "playbook": runner.playbooks[incumbent.playbook].to_doc(),
        "baseline": baseline.doc(),
        "rerere": final.get("rerere", {}),
        "ucb": stats,
        "llm_usage": asdict(llm.USAGE),
        "created": time.time(),
    }
    (store.dir / "final.json").write_text(json.dumps(out, indent=1, default=str))
    store.add("evals", {"kind": "final_validation", **out})
    store.close()
    await llm.close()
    return store.dir


def main(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="forkward explore")
    p.add_argument("--warm", default="search-2")
    p.add_argument("--rounds", type=int, default=4)
    p.add_argument("--run-id", default=None)
    p.add_argument("--ucb-from", default=None, help="carry arm rewards from an earlier explore run")
    a = p.parse_args(argv)
    print(f"run written to {asyncio.run(explore(a.warm, a.rounds, a.run_id, a.ucb_from))}")
    return 0
