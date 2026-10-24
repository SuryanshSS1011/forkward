"""Candidate clustering, selection by measured agreement, and the safety gate."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

from forkward.algebra import Candidate
from forkward.score import class_of_candidate, clopper_pearson_upper, normalize

ABSTAIN = 1.01  # above any confidence, so nothing is accepted
# The pre-declared threshold grid for fixed-sequence testing, most conservative first. It never
# depends on calibration data, unlike a grid built from the observed confidences.
FIXED_GRID = tuple(round(1 - i / 100, 2) for i in range(100))


def ltt_method() -> str:
    """The product gate uses fixed-sequence testing. FORKWARD_LTT_METHOD=bonferroni restores the
    method the original experiments were registered with. Read at call time, so a
    value in .env (loaded after import) counts."""
    return os.environ.get("FORKWARD_LTT_METHOD", "fixed_sequence")


@dataclass
class Cluster:
    lines: tuple[str, ...]
    ops: list[str] = field(default_factory=list)
    # Operators whose input was complete. Only these satisfy the gate's operator requirement.
    voting_ops: list[str] = field(default_factory=list)
    personas: list[str] = field(default_factory=list)
    logprobs: list[float] = field(default_factory=list)
    label: str = "merge"


@dataclass
class Decision:
    lines: tuple[str, ...] | None
    source: str
    confidence: float
    has_op: bool
    classifier: str | None
    classifier_agrees: bool | None
    margin: int
    n_proposers: int
    disagreement: bool
    clusters: list[Cluster]

    def accepted(self, threshold: float, need_classifier: bool) -> bool:
        if self.lines is None or not self.has_op or self.margin < 1:
            return False
        if need_classifier and not self.classifier_agrees:
            return False
        return self.confidence >= threshold


def cluster(
    vendor: tuple[str, ...],
    upstream: tuple[str, ...],
    ops: list[Candidate],
    proposals: list[tuple[str, tuple[str, ...] | None, float | None]],
) -> list[Cluster]:
    """Group candidates whose normalized lines are identical."""
    by_key: dict[tuple[str, ...], Cluster] = {}
    for c in ops:
        cl = by_key.setdefault(normalize(c.lines), Cluster(c.lines))
        cl.ops.append(c.source)
        if c.vote:
            cl.voting_ops.append(c.source)
    for persona, lines, lp in proposals:
        if lines is None:
            continue
        k = normalize(lines)
        cl = by_key.setdefault(k, Cluster(lines))
        cl.personas.append(persona)
        if lp is not None:
            cl.logprobs.append(lp)
    for cl in by_key.values():
        cl.label = class_of_candidate(cl.lines, vendor, upstream)
    return list(by_key.values())


def select(
    clusters: list[Cluster], n_proposers: int, classifier: str | None, rule: dict
) -> Decision:
    w_llm = rule.get("w_llm", 1.0)
    w_cls = rule.get("w_cls", 0.0)
    priors: dict[str, float] = rule.get("op_prior", {})

    def score(cl: Cluster) -> tuple[float, int]:
        s = w_llm * len(cl.personas) + sum(priors.get(op, 0.0) for op in cl.ops)
        if classifier and cl.label == classifier:
            s += w_cls
        return s, int(cl.label == "keep_vendor")

    live = [c for c in clusters if c.personas or any(priors.get(op, 0.0) > 0 for op in c.ops)]
    if not live:
        return Decision(None, "none", 0.0, False, classifier, None, 0, n_proposers, False, clusters)
    ranked = sorted(live, key=score, reverse=True)
    win = ranked[0]
    votes = sorted((len(c.personas) for c in clusters), reverse=True)
    runner_up = max((len(c.personas) for c in clusters if c is not win), default=0)
    share = len(win.personas) / n_proposers if n_proposers else 0.0
    if win.logprobs:
        share *= math.exp(sum(win.logprobs) / len(win.logprobs))
    source = "+".join(win.ops + [f"llm:{p}" for p in win.personas]) or "none"
    return Decision(
        lines=win.lines,
        source=source,
        confidence=share,
        has_op=bool(win.voting_ops),
        classifier=classifier,
        classifier_agrees=None if classifier is None else classifier == win.label,
        margin=len(win.personas) - runner_up,
        n_proposers=n_proposers,
        disagreement=sum(1 for v in votes if v > 0) > 1,
        clusters=clusters,
    )


def calibrate(
    decisions: dict[str, Decision],
    correct: dict[str, bool],
    need_classifier: bool,
    target: float,
    confidence: float = 0.95,
    grid: int = 10,
    method: str | None = None,
) -> dict:
    """Learn-then-Test over a threshold grid with a one-sided Clopper-Pearson bound.

    The default is fixed-sequence testing on FIXED_GRID (see `_fixed_sequence`); `method=
    "bonferroni"` keeps the earlier Bonferroni test over a grid built from the observed
    confidences. Either way, when no threshold meets the target the gate abstains: the returned
    threshold is ABSTAIN, so nothing auto-applies, and the closest grid row is reported under
    `best_effort` for diagnostics only.
    """

    def accepted_at(t: float) -> list[str]:
        return [
            tag for tag, d in decisions.items() if tag in correct and d.accepted(t, need_classifier)
        ]

    confs = [d.confidence for d in decisions.values()]
    return _calibrate(confs, accepted_at, correct, target, confidence, grid, method)


def _calibrate(confs, accepted_at, correct, target, confidence, grid, method) -> dict:
    if (method or ltt_method()) == "bonferroni":
        return _ltt(_grid(confs, grid), accepted_at, correct, target, confidence)
    return _fixed_sequence(FIXED_GRID, accepted_at, correct, target, confidence)


def _fixed_sequence(thresholds, accepted_at, correct, target: float, confidence: float) -> dict:
    """Fixed-sequence Learn-then-Test. Thresholds are tested from the most conservative down, each
    at the full level `confidence`, and testing stops at the first threshold whose bound misses
    the target; the lowest threshold passed is returned. The family-wise error rate stays at
    1 - confidence without a Bonferroni split, because the order is fixed in advance.

    Testing starts at the first threshold that accepts at least `min_accepted_for(target)` hunks,
    since fewer can never pass. That start depends only on the confidences, never on the labels,
    so the order is still fixed given the scores."""
    need = min_accepted_for(target, confidence)
    rows, passed = [], None
    for t in sorted(set(thresholds), reverse=True):
        acc = accepted_at(t)
        if len(acc) < need and passed is None:
            continue
        errors = sum(not correct[tag] for tag in acc)
        row = {
            "threshold": t,
            "accepted": len(acc),
            "errors": errors,
            "bound": clopper_pearson_upper(errors, len(acc), confidence),
        }
        rows.append(row)
        if row["bound"] > target:
            break
        passed = row
    if passed:
        return {
            **passed,
            "target_met": True,
            "level": confidence,
            "method": "fixed_sequence",
            "grid": rows,
        }
    out = {
        "threshold": ABSTAIN,
        "target_met": False,
        "bound": 1.0,
        "accepted": 0,
        "errors": 0,
        "level": confidence,
        "method": "fixed_sequence",
        "grid": rows,
    }
    if rows:
        out["best_effort"] = min(rows, key=lambda r: (r["bound"], -r["accepted"]))
    else:
        # No threshold accepted enough hunks to be tested. Report the loosest one, for scale.
        acc = accepted_at(min(thresholds))
        errors = sum(not correct[tag] for tag in acc)
        out["best_effort"] = {
            "threshold": min(thresholds),
            "accepted": len(acc),
            "errors": errors,
            "bound": clopper_pearson_upper(errors, len(acc), confidence),
            "note": f"fewer than {need} hunks accepted at any threshold, so none was tested",
        }
    return out


def _grid(confs: list[float], grid: int) -> list[float]:
    confs = sorted({round(c, 6) for c in confs})
    if not confs:
        return []
    step = max(1, len(confs) // grid)
    return sorted(set(confs[::step] + [confs[-1]]))


def _ltt(thresholds, accepted_at, correct: dict[str, bool], target: float, confidence: float):
    if not thresholds:
        return {"threshold": ABSTAIN, "target_met": False, "bound": 1.0, "accepted": 0, "errors": 0}
    level = 1 - (1 - confidence) / len(thresholds)
    rows = []
    for t in thresholds:
        acc = accepted_at(t)
        errors = sum(not correct[tag] for tag in acc)
        rows.append(
            {
                "threshold": t,
                "accepted": len(acc),
                "errors": errors,
                "bound": clopper_pearson_upper(errors, len(acc), level),
            }
        )
    ok = [r for r in rows if r["bound"] <= target]
    if ok:
        best = min(ok, key=lambda r: r["threshold"])
        return {**best, "target_met": True, "level": level, "grid": rows}
    best = min(rows, key=lambda r: (r["bound"], -r["accepted"]))
    return {
        "threshold": ABSTAIN,
        "target_met": False,
        "bound": 1.0,
        "accepted": 0,
        "errors": 0,
        "level": level,
        "best_effort": best,
        "grid": rows,
    }


def calibrate_scores(
    scores: dict[str, float],
    correct: dict[str, bool],
    target: float,
    confidence: float = 0.95,
    grid: int = 10,
    method: str | None = None,
) -> dict:
    """The same Learn-then-Test procedure for a plain score, accepting a hunk when its score is at
    least the threshold. Used for the operator selector and for synthetic checks."""
    tags = [t for t in scores if t in correct]

    def accepted_at(t: float) -> list[str]:
        return [tag for tag in tags if scores[tag] >= t]

    confs = [scores[t] for t in tags]
    return _calibrate(confs, accepted_at, correct, target, confidence, grid, method)


def calibrate_groups(
    scores: dict[str, float],
    correct: dict[str, bool],
    groups: dict[str, str],
    target: float,
    confidence: float = 0.95,
    grid: int = 10,
    method: str | None = None,
) -> dict:
    """Group-conditional calibration: one Learn-then-Test threshold per group.

    Each group runs at level 1 - (1 - confidence) / G, so the bounds hold for all groups at once
    with the stated confidence. A group whose target is not met abstains on its own, without
    taking threshold room from the others. `worst_bound` is the largest certified bound among
    groups that accept anything.
    """
    names = sorted({groups[t] for t in scores if t in groups})
    level = 1 - (1 - confidence) / max(1, len(names))
    per = {}
    for g in names:
        sub = {t: v for t, v in scores.items() if groups.get(t) == g}
        per[g] = calibrate_scores(sub, correct, target, level, grid, method)
    certified = [c for c in per.values() if c["target_met"] and c["accepted"]]
    return {
        "groups": per,
        "level": level,
        "target_met_groups": sorted(g for g, c in per.items() if c["target_met"]),
        "worst_bound": max((c["bound"] for c in certified), default=None),
    }


def accept_grouped(score: float, group: str, calibration: dict) -> bool:
    """Accept only under a certified threshold for this hunk's group."""
    return score >= usable_threshold(calibration["groups"].get(group))


def leave_one_group_out(
    scores: dict[str, float],
    correct: dict[str, bool],
    vendor: dict[str, str],
    target: float,
    confidence: float = 0.95,
    groups: dict[str, str] | None = None,
) -> dict:
    """Leave-one-vendor-out calibration. For each vendor, calibrate on all other vendors (per
    group when `groups` is given, pooled otherwise), apply the threshold to the held-out vendor,
    and record its realized escaped error and an empirical Clopper-Pearson bound. The worst
    held-out vendor is the honest summary of what the gate guarantees under vendor shift."""
    folds = {}
    for v in sorted(set(vendor.values())):
        cal = {t: s for t, s in scores.items() if vendor.get(t) != v}
        held = [t for t in scores if vendor.get(t) == v and t in correct]
        if groups:
            c = calibrate_groups(cal, correct, groups, target, confidence)
            acc = [t for t in held if accept_grouped(scores[t], groups.get(t, ""), c)]
        else:
            c = calibrate_scores(cal, correct, target, confidence)
            acc = [t for t in held if scores[t] >= usable_threshold(c)]
        errors = sum(not correct[t] for t in acc)
        folds[v] = {
            "n": len(held),
            "accepted": len(acc),
            "errors": errors,
            "escaped_rate": errors / len(acc) if acc else 0.0,
            "bound": clopper_pearson_upper(errors, len(acc), confidence),
            "violates_target": bool(acc) and errors / len(acc) > target,
        }
    live = {v: f for v, f in folds.items() if f["accepted"]}
    worst = max(live, key=lambda v: live[v]["bound"], default=None)
    return {
        "folds": folds,
        "worst_vendor": worst,
        "worst_bound": live[worst]["bound"] if worst else None,
        "violations": sum(f["violates_target"] for f in folds.values()),
    }


def min_accepted_for(target: float, confidence: float = 0.95) -> int:
    """Smallest number of error-free accepted hunks whose one-sided Clopper-Pearson bound is at
    most `target`. Below this, no calibration set can certify the target."""
    n = 1
    while clopper_pearson_upper(0, n, confidence) > target:
        n += 1
    return n


def usable_threshold(calibration: dict | None) -> float:
    """The threshold a deployment may act on: only one whose calibration met its target."""
    if not calibration or not calibration.get("target_met"):
        return ABSTAIN
    return calibration["threshold"]
