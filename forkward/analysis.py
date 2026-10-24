"""Attribution from logged runs. Replays selection over logged proposals, so it makes no model calls.

The ladder adds one component at a time on the same hunks, so the paired bootstrap between rungs
measures what each component earned: deterministic operators, the proposer team, the learned
selection rule, and the learned playbook.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

from forkward import algebra, gate, score, stats
from forkward.data import load_hunks
from forkward.store import RUNS, read_jsonl


def logged(run: Path, split: str) -> tuple[dict, dict]:
    """Proposals keyed by (playbook, persona, tag) and classifier labels keyed by (playbook, tag)."""
    props: dict = {}
    labels: dict = {}
    for a in read_jsonl(run, "attempts"):
        if a.get("split") != split:
            continue
        if a.get("kind") == "propose":
            lines = tuple(a["lines"]) if a.get("lines") is not None else None
            props[(a["playbook"], a["persona"], a["tag"])] = (lines, a.get("mean_logprob"))
        elif a.get("kind") == "classify":
            labels[(a["playbook"], a["tag"])] = a.get("label")
    return props, labels


def replay(run: Path, split: str, personas: list[str], playbook: str, rule: dict, use_cls: bool):
    """Per-hunk correctness for a strategy whose proposals were all logged."""
    key = score.load_key(split)
    hunks = {h.tag: h for h in load_hunks(split, allow_test=split == "test")}
    props, labels = logged(run, split)
    out = {}
    for tag, e in key.items():
        h = hunks[tag]
        if any((playbook, p, tag) not in props for p in personas):
            return None
        cl = gate.cluster(
            h.vendor,
            h.upstream,
            algebra.candidates(
                h, token=bool(rule.get("token_ops")), fuzzy=bool(rule.get("fuzzy_ops"))
            ),
            [(p, *props[(playbook, p, tag)]) for p in personas],
        )
        d = gate.select(cl, len(personas), labels.get((playbook, tag)) if use_cls else None, rule)
        out[tag] = float(score.is_correct(d.lines, e))
    return out


def ladder(run_id: str, split: str = "validation") -> dict:
    run = RUNS / run_id
    strategies = {d["id"]: d for d in read_jsonl(run, "strategies")}
    final = json.loads((run / "final.json").read_text())
    fs = final["strategy"]
    seed = next(
        d
        for d in strategies.values()
        if d["round"] == 0 and d["personas"] == fs["personas"] and d["playbook"] == "pb-0"
    )
    key = score.load_key(split)
    hunks = {h.tag: h for h in load_hunks(split, allow_test=split == "test")}
    ops_only = {t: float(score.is_correct(hunks[t].vendor, e)) for t, e in key.items()}
    rungs = [
        ("Always keep vendor (no model)", ops_only),
        (
            "Zero-shot single resolver",
            replay(run, split, ["zero_shot"], "pb-0", {"w_llm": 1.0, "op_prior": {}}, False),
        ),
        (
            "Team + operators + vote, no learning",
            replay(run, split, fs["personas"], "pb-0", seed["rule"], seed["use_classifier"]),
        ),
        (
            "+ learned selection rule",
            replay(run, split, fs["personas"], "pb-0", fs["rule"], fs["use_classifier"]),
        ),
        (
            "+ learned playbook (frozen strategy)",
            replay(run, split, fs["personas"], fs["playbook"], fs["rule"], fs["use_classifier"]),
        ),
    ]
    playbook_only = replay(
        run, split, fs["personas"], fs["playbook"], seed["rule"], seed["use_classifier"]
    )
    clusters = stats.clusters_for(split)
    rows = []
    prev = None
    for name, per in rungs:
        if per is None:
            rows.append({"rung": name, "missing": True})
            continue
        row = {"rung": name, "correct": int(sum(per.values())), "n": len(per)}
        row["accuracy"] = row["correct"] / row["n"]
        row["wilson"] = stats.wilson(row["correct"], row["n"])
        if prev is not None:
            row["vs_previous"] = stats.compare(prev, per, clusters, 5000)
        rows.append(row)
        prev = per
    stats.adjust({r["rung"]: r.get("vs_previous") for r in rows})
    team, learned, keep_vendor = rungs[2][1], rungs[4][1], rungs[0][1]

    def cmp(a, b):
        return stats.compare(a, b, clusters, 5000) if a and b else None

    attribution = stats.adjust(
        {
            "harness_gain": cmp(rungs[1][1], team),
            "learning_gain": cmp(team, learned),
            "playbook_alone": cmp(team, playbook_only),
            "rule_alone": cmp(team, rungs[3][1]),
            "frozen_vs_keep_vendor": cmp(keep_vendor, learned),
        }
    )
    return {"run": run_id, "split": split, "ladder": rows, "attribution": attribution}


def lineage(run_id: str) -> list[dict]:
    """Every playbook version with its parent, its lessons and how its strategy scored."""
    run = RUNS / run_id
    by_pb: dict = collections.defaultdict(dict)
    for d in read_jsonl(run, "strategies"):
        s = by_pb[d["playbook"]]
        s.setdefault("strategies", []).append(d["id"])
        if d.get("train"):
            s["train"] = max(s.get("train", 0), d["train"].get("accuracy", 0))
        if d.get("validation"):
            s["validation"] = d["validation"].get("accuracy")
        if d.get("promotion"):
            s["promotion"] = d["promotion"]
    out = []
    seen = set()
    for pv in read_jsonl(run, "playbook_versions"):
        if pv["version"] in seen:
            continue
        seen.add(pv["version"])
        out.append(
            {
                "version": pv["version"],
                "parent": pv.get("parent"),
                "round": pv.get("round", 0),
                "lessons": [x["text"] for x in pv.get("lessons", [])],
                "rejected": len(pv.get("rejected", [])),
                **by_pb.get(pv["version"], {}),
            }
        )
    return out


def main(argv: list[str]) -> int:
    run_id = argv[0] if argv else "search-2"
    split = argv[1] if len(argv) > 1 else "validation"
    if split == "test":
        raise SystemExit(
            "the ladder runs on train or validation; the test split was evaluated once"
        )
    res = ladder(run_id, split)
    for r in res["ladder"]:
        if r.get("missing"):
            print(f"{r['rung']:<40} not logged")
            continue
        lo, hi = r["wilson"]
        print(
            f"{r['rung']:<40} {r['correct']:>3}/{r['n']} ({r['accuracy']:.1%}, "
            f"Wilson [{lo:.1%}, {hi:.1%}])"
        )
        if r.get("vs_previous"):
            print("    " + stats.render("vs previous rung", r["vs_previous"], 20))
    print("Attribution (Holm across this family):")
    for k, d in res["attribution"].items():
        print("  " + stats.render(k, d, 22))
    out = Path("results") / f"ladder-{run_id}-{split}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({**res, "lineage": lineage(run_id)}, indent=1))
    print(f"written {out}")
    return 0


def parallelism(run_id: str, split: str = "validation") -> dict:
    """Do parallel proposers find better answers than any one of them alone? From logs only.

    Compares each persona alone (its own answer taken as final) with the team's vote over the
    same proposals, and reports the ceiling where some parallel agent or operator produced the
    reference, which measures the diversity the team adds.
    """
    if split == "test":
        raise ValueError("parallelism analysis runs on train or validation")
    run = RUNS / run_id
    final = json.loads((run / "final.json").read_text())
    fs = final["strategy"]
    key = score.load_key(split)
    hunks = {h.tag: h for h in load_hunks(split)}
    props, _ = logged(run, split)
    pb = fs["playbook"]
    solo = {
        p: {
            t: float(score.is_correct((props.get((pb, p, t)) or (None,))[0], e))
            for t, e in key.items()
        }
        for p in fs["personas"]
    }
    team = replay(run, split, fs["personas"], pb, fs["rule"], fs["use_classifier"]) or {}
    any_agent = {
        t: float(any(solo[p][t] for p in fs["personas"]) or score.is_correct(hunks[t].vendor, e))
        for t, e in key.items()
    }
    best_solo = max(solo, key=lambda p: sum(solo[p].values()))
    n = len(key)
    return {
        "run": run_id,
        "split": split,
        "n": n,
        "solo": {p: sum(v.values()) / n for p, v in solo.items()},
        "best_solo": best_solo,
        "team_vote": sum(team.values()) / n,
        "team_vs_best_solo": stats.compare(solo[best_solo], team, stats.clusters_for(split), 5000)
        if team
        else None,
        "any_agent_or_operator": sum(any_agent.values()) / n,
    }


FAMILIES = {
    "vendor_first": "direct rewrite",
    "upstream_fix_first": "direct rewrite",
    "refactor_aware": "direct rewrite",
    "minimal_diff": "direct rewrite",
    "operator_picker": "LLM picks an operator",
    "zero_shot": "zero-shot rewrite",
}


def population(run_id: str) -> dict:
    """Search-level parallelism from logs: best-so-far by round and wall-clock, best-of-k by
    subsampling each round's population (an approximation that ignores feedback a smaller
    population would have received), and the oracle over every strategy scored on validation."""
    import itertools
    from datetime import datetime

    run = RUNS / run_id
    rounds = read_jsonl(run, "rounds")
    events = read_jsonl(run, "events")
    start = min(e["t"] for e in events if "t" in e) if events else None
    curve = []
    for r in rounds:
        t = datetime.fromisoformat(r["ts"]).timestamp()
        curve.append(
            {
                "round": r["round"],
                "minutes": round((t - start) / 60, 1) if start else None,
                "best_train": r["best_train"]["accuracy"],
                "incumbent_validation": r["incumbent_validation"]["accuracy"],
            }
        )
    strategies = read_jsonl(run, "strategies")
    by_round: dict = collections.defaultdict(dict)
    for s in strategies:
        if s.get("train") and s["id"] != "s-baseline":
            by_round[s["round"]][s["id"]] = s["train"]["accuracy"]
    best_of_k = {}
    for r, scores in sorted(by_round.items()):
        vals = list(scores.values())
        rows = {}
        for k in range(1, len(vals) + 1):
            combos = list(itertools.combinations(vals, k))
            rows[k] = sum(max(c) for c in combos) / len(combos)
        best_of_k[r] = rows
    key = score.load_key("validation")
    val_strats = [s for s in strategies if s.get("validation") and s["id"] != "s-baseline"]
    per = {}
    for s in val_strats:
        rep = replay(
            run, "validation", s["personas"], s["playbook"], s["rule"], s["use_classifier"]
        )
        if rep:
            per[s["id"]] = rep
    oracle = sum(1 for t in key if any(p[t] for p in per.values())) / len(key) if per else None
    best_single = max((sum(p.values()) / len(key) for p in per.values()), default=None)
    return {
        "run": run_id,
        "curve": curve,
        "best_of_k_train": best_of_k,
        "validation_strategies": len(per),
        "oracle_validation": oracle,
        "best_single_validation": best_single,
    }


def lesson_flips(run_id: str) -> list[dict]:
    """Training hunks a child got right and its parent got wrong, where a resolver cited a lesson
    the child's playbook added. Shows feedback from round N changing answers in round N+1."""
    run = RUNS / run_id
    strategies = {s["id"]: s for s in read_jsonl(run, "strategies")}
    pvs = {}
    for pv in read_jsonl(run, "playbook_versions"):
        pvs.setdefault(pv["version"], pv)
    key = score.load_key("train")
    cites: dict = collections.defaultdict(set)
    for a in read_jsonl(run, "attempts"):
        if a.get("kind") == "propose" and a.get("split") == "train":
            for lid in a.get("lessons_used") or []:
                cites[(a["playbook"], a["tag"])].add(lid)
    out = []
    for s in strategies.values():
        parent = strategies.get(s.get("parent") or "")
        if not parent or s["playbook"] == parent["playbook"]:
            continue
        old = {x["id"] for x in pvs.get(parent["playbook"], {}).get("lessons", [])}
        new = {
            x["id"]: x["text"]
            for x in pvs.get(s["playbook"], {}).get("lessons", [])
            if x["id"] not in old
        }
        a = replay(
            run,
            "train",
            parent["personas"],
            parent["playbook"],
            parent["rule"],
            parent["use_classifier"],
        )
        b = replay(run, "train", s["personas"], s["playbook"], s["rule"], s["use_classifier"])
        if not a or not b:
            continue
        for lid, text in new.items():
            flipped = [t for t in key if not a[t] and b[t] and lid in cites[(s["playbook"], t)]]
            broke = [t for t in key if a[t] and not b[t] and lid in cites[(s["playbook"], t)]]
            if flipped or broke:
                out.append(
                    {
                        "round": s["round"],
                        "strategy": s["id"],
                        "parent": parent["id"],
                        "lesson": text,
                        "fixed": flipped,
                        "broke": broke,
                    }
                )
    out.sort(key=lambda x: len(x["fixed"]) - len(x["broke"]), reverse=True)
    return out


def family_accuracy(run_id: str, split: str = "train") -> list[dict]:
    """Accuracy of each family of approach per playbook version: deterministic operator alone,
    each rewrite persona alone, the operator-picking persona, and the team vote."""
    run = RUNS / run_id
    key = score.load_key(split)
    hunks = {h.tag: h for h in load_hunks(split)}
    props, _ = logged(run, split)
    rows = []
    seen = set()
    for s in read_jsonl(run, "strategies"):
        if s["playbook"] in seen or s["id"] == "s-baseline" or not s.get("train"):
            continue
        seen.add(s["playbook"])
        fam: dict = {
            "deterministic operator (keep vendor)": sum(
                score.is_correct(hunks[t].vendor, e) for t, e in key.items()
            )
            / len(key)
        }
        for p in s["personas"]:
            got = [props.get((s["playbook"], p, t)) for t in key]
            if any(g is None for g in got):
                continue
            acc = sum(
                score.is_correct(g[0], e) for g, e in zip(got, key.values(), strict=True)
            ) / len(key)
            name = f"{FAMILIES.get(p, p)}: {p.replace('_', ' ')}"
            fam[name] = acc
        team = replay(run, split, s["personas"], s["playbook"], s["rule"], s["use_classifier"])
        if team:
            fam["team vote"] = sum(team.values()) / len(key)
        rows.append(
            {
                "round": s["round"],
                "playbook": s["playbook"],
                "lessons": len(
                    next(
                        (
                            pv.get("lessons", [])
                            for pv in read_jsonl(run, "playbook_versions")
                            if pv["version"] == s["playbook"]
                        ),
                        [],
                    )
                ),
                "families": fam,
            }
        )
    return rows


# Files in results/ that `report` does not rebuild, and the command that writes each one.
OTHER_SOURCES = {
    "test-results.json": "`python -m forkward test`, copied from runs/test-*/results.json. "
    "The test split is evaluated once, so report never rewrites it.",
    "leakage-audit.txt": "`python -m forkward audit > results/leakage-audit.txt`, captured "
    "before the test run as evidence. Report never rewrites it.",
    "parallel-speed-benchmark.json": "a timing script that is not in this repository. The "
    "compute-matched experiment now records wall-clock per arm instead.",
    "experiment-compute-matched.json": "`python -m forkward experiment compute-matched` "
    "(model calls).",
    "experiment-no-reflector.json": "`python -m forkward experiment no-reflector` (model calls).",
}


def learning_curve(run_id: str) -> dict:
    """Round-by-round best train accuracy and incumbent validation accuracy of a search run."""
    rounds = read_jsonl(RUNS / run_id, "rounds")
    first = rounds[0] if rounds else {}
    return {
        "run": run_id,
        "baseline_round0": first.get("baseline"),
        "keep_vendor": first.get("keep_vendor"),
        "rounds": [
            {
                "round": r["round"],
                "best_train_accuracy": r["best_train"]["accuracy"],
                "children_train_mean": r.get("children_train_mean"),
                "incumbent": r.get("incumbent"),
                "incumbent_validation_accuracy": r["incumbent_validation"]["accuracy"],
                "promotion": r.get("promotion"),
            }
            for r in rounds
        ],
    }


def explore_summary(run_id: str) -> dict:
    rounds = read_jsonl(RUNS / run_id, "rounds")
    f = json.loads((RUNS / run_id / "final.json").read_text())
    return {
        "run": run_id,
        "warm_start": f.get("warm_start"),
        "rounds": [
            {
                "round": x["round"],
                "expansions": x.get("expansions", []),
                "promotion": (x.get("promotion") or {}).get("reason"),
            }
            for x in rounds
        ],
        "ucb": f.get("ucb"),
    }


def _generators() -> list[tuple[str, list[Path], object]]:
    """(results file, inputs it needs, function that builds it). No model calls."""
    from forkward import oracle, selector

    gens: list[tuple[str, list[Path], object]] = [
        ("operators.json", [], lambda: [oracle.table(s) for s in oracle.SPLITS]),
        ("selector-h0.json", [], lambda: selector.run()),
    ]
    for r in ("search-2", "search-3"):
        for split in ("train", "validation"):
            gens.append(
                (
                    f"ladder-{r}-{split}.json",
                    [RUNS / r / "final.json"],
                    lambda r=r, split=split: {**ladder(r, split), "lineage": lineage(r)},
                )
            )
    for split in ("train", "validation"):
        gens.append(
            (
                f"parallelism-search-2-{split}.json",
                [RUNS / "search-2" / "final.json"],
                lambda split=split: parallelism("search-2", split),
            )
        )
    gens.append(
        (
            "learning-curve.json",
            [RUNS / "search-2" / "rounds.jsonl"],
            lambda: learning_curve("search-2"),
        )
    )
    gens.append(
        (
            "population-and-families.json",
            [RUNS / "search-2" / "final.json"],
            lambda: {
                "population": {
                    r: population(r)
                    for r in ("search-2", "search-3", "control-pop1")
                    if (RUNS / r / "rounds.jsonl").exists()
                },
                "lesson_flips": lesson_flips("search-2"),
                "families_search2": family_accuracy("search-2"),
            },
        )
    )
    for r in ("explore-1", "explore-2"):
        gens.append((f"{r}.json", [RUNS / r / "final.json"], lambda r=r: explore_summary(r)))
    return gens


def report() -> list[dict]:
    """Rebuild every results file whose inputs are present and say where every other file in
    results/ comes from. Writes results/PROVENANCE.json. Makes no model calls."""
    out = Path("results")
    out.mkdir(exist_ok=True)
    rows = []
    for name, inputs, build in _generators():
        missing = [f"runs/{p.relative_to(RUNS).as_posix()}" for p in inputs if not p.exists()]
        if missing:
            rows.append({"file": name, "status": "skipped", "source": f"missing {missing}"})
            continue
        (out / name).write_text(json.dumps(build(), indent=1, default=str))
        rows.append({"file": name, "status": "regenerated", "source": "python -m forkward report"})
    built = {r["file"] for r in rows}
    for p in sorted(out.iterdir()):
        if p.name in built or p.name == "PROVENANCE.json":
            continue
        src = OTHER_SOURCES.get(p.name, "unknown: no generating code is registered for this file")
        rows.append({"file": p.name, "status": "from another command", "source": src})
    # Shipped with the run logs (attempts stay local), so the board shows real lesson records.
    for d in sorted(RUNS.iterdir()) if RUNS.exists() else []:
        if (d / "attempts.jsonl").exists() and (d / "playbook_versions.jsonl").exists():
            (d / "lesson_stats.json").write_text(json.dumps(lesson_stats(d.name)))
            rows.append(
                {
                    "file": f"../runs/{d.name}/lesson_stats.json",
                    "status": "regenerated",
                    "source": "python -m forkward report",
                }
            )
    (out / "PROVENANCE.json").write_text(json.dumps(rows, indent=1))
    return rows


def report_main(argv: list[str]) -> int:
    rows = report()
    for r in rows:
        print(f"{r['status']:<21} results/{r['file']}  ({r['source']})")
    skipped = [r for r in rows if r["status"] == "skipped"]
    print(
        f"\n{sum(r['status'] == 'regenerated' for r in rows)} regenerated, {len(skipped)} skipped"
    )
    return 1 if skipped and "--strict" in argv else 0


def lesson_stats(run_id: str) -> dict:
    """Per lesson: how often resolvers cited it on training hunks, and how often the citing
    proposal was right (helpful) or wrong (harmful). Computed from logged calls, so it is correct
    for every playbook version, including ones made in a run's last round."""
    run = RUNS / run_id
    key = score.load_key("train")
    out: dict = {}
    for a in read_jsonl(run, "attempts"):
        if a.get("kind") != "propose" or a.get("split") != "train" or a.get("tag") not in key:
            continue
        lines = tuple(a["lines"]) if a.get("lines") is not None else None
        ok = score.is_correct(lines, key[a["tag"]])
        for lid in a.get("lessons_used") or []:
            s = out.setdefault(lid, {"cited": 0, "helpful": 0, "harmful": 0})
            s["cited"] += 1
            s["helpful" if ok else "harmful"] += 1
    return out
