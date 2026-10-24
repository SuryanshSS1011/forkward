"""Operator coverage on train and validation, before and after the delta-operator fix.

For each operator it counts the scorable hunks where the operator produced a candidate, where
that candidate equals the vendor's reference, and how many of those candidates may vote in the
gate. For each operator set it reports oracle coverage, the share of hunks where at least one
operator is correct, overall and per class. It makes no model calls and never reads test.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from forkward import algebra, legacy_algebra, score
from forkward.algebra import Candidate
from forkward.data import Hunk, load_hunks

SPLITS = ("train", "validation")
SETS = ("before", "after", "after+fuzzy", "after+fuzzy+token")


def legacy_candidates(h: Hunk) -> list[Candidate]:
    """The operator set as it was before the fix, where every operator could vote."""
    out = []
    for name, op in algebra.OPERATORS.items():
        fn = getattr(legacy_algebra, name, op)
        if (lines := fn(h)) is not None:
            out.append(Candidate(tuple(lines), f"op:{name}", True, True))
    return out


def operator_sets(h: Hunk) -> dict[str, list[Candidate]]:
    return {
        "before": legacy_candidates(h),
        "after": algebra.candidates(h),
        "after+fuzzy": algebra.candidates(h, fuzzy=True),
        "after+fuzzy+token": algebra.candidates(h, fuzzy=True, token=True),
    }


def _op_rows(per: dict, ops: list[str]) -> list[dict]:
    rows = []
    for op in ops:
        b, a = per["before"].get(op), per["after+fuzzy+token"].get(op)
        rows.append({"operator": op.removeprefix("op:"), "before": b, "after": a})
    return rows


def table(split: str) -> dict:
    from forkward.data import parse_v2

    v2 = parse_v2(split)
    if split not in SPLITS and not (v2 and v2[0] == "dev"):
        raise ValueError("operator coverage runs on train, validation or the v2 dev pool only")
    key = score.load_key(split)
    hunks = [h for h in load_hunks(split) if h.tag in key]
    classes = Counter(e.label for e in key.values())
    per: dict = {s: {} for s in SETS}
    oracle: dict = {s: Counter() for s in SETS}
    for h in hunks:
        e = key[h.tag]
        for name, cands in operator_sets(h).items():
            hits = [c for c in cands if score.is_correct(c.lines, e)]
            oracle[name]["any"] += bool(hits)
            oracle[name]["any_voting"] += any(c.vote for c in hits)
            oracle[name][f"any:{e.label}"] += bool(hits)
            for c in cands:
                row = per[name].setdefault(
                    c.source, {"produced": 0, "correct": 0, "voting": 0, "voting_correct": 0}
                )
                ok = score.is_correct(c.lines, e)
                row["produced"] += 1
                row["correct"] += ok
                row["voting"] += c.vote
                row["voting_correct"] += c.vote and ok
                row[f"correct:{e.label}"] = row.get(f"correct:{e.label}", 0) + ok
    ops = list(dict.fromkeys(op for s in SETS for op in per[s]))
    return {
        "split": split,
        "n": len(hunks),
        "classes": dict(classes),
        "operators": _op_rows(per, ops),
        "oracle": {
            s: {
                "any": oracle[s]["any"],
                "any_voting": oracle[s]["any_voting"],
                "per_class": {c: oracle[s][f"any:{c}"] for c in score.CLASSES},
            }
            for s in SETS
        },
    }


def render(t: dict) -> str:
    cls = ", ".join(f"{k} {v}" for k, v in sorted(t["classes"].items()))
    lines = [
        f"{t['split']}: {t['n']} scorable hunks ({cls})",
        f"{'operator':<30}{'before made/correct':>21}{'after made/correct':>20}{'after voting':>14}",
    ]
    for r in t["operators"]:
        b, a = r["before"], r["after"]
        bs = f"{b['produced']}/{b['correct']}" if b else "-"
        as_ = f"{a['produced']}/{a['correct']}" if a else "-"
        vs = f"{a['voting']}/{a['voting_correct']}" if a else "-"
        lines.append(f"{r['operator']:<30}{bs:>21}{as_:>20}{vs:>14}")
    lines.append(f"{'oracle coverage':<20}{'any correct':>14}{'voting correct':>16}  per class")
    for s, o in t["oracle"].items():
        pc = " ".join(f"{c} {o['per_class'][c]}/{t['classes'].get(c, 0)}" for c in score.CLASSES)
        lines.append(f"{s:<20}{o['any']:>8}/{t['n']:<5}{o['any_voting']:>10}/{t['n']:<5}  {pc}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    splits = [a for a in argv if not a.startswith("-")] or list(SPLITS)
    if "test" in splits or any(s.startswith("holdout") for s in splits):
        raise SystemExit("operator coverage never reads a test or holdout pool")
    tables = [table(s) for s in splits]
    for t in tables:
        print(render(t) + "\n")
    v2 = any(":" in s or s == "dev" for s in splits)
    out = Path("results") / ("operators-v2.json" if v2 else "operators.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(tables, indent=1))
    print(f"written {out}")
    return 0
