"""Deterministic scorer. The only module that reads key files."""

from __future__ import annotations

import json
import math
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from forkward.data import SPLITS, data_dir, parse_v2, v2_dir, v2_records, verify_hashes

CLASSES = ("keep_vendor", "take_upstream", "merge")


def normalize(lines: Iterable[str]) -> tuple[str, ...]:
    return tuple(s for s in (line.strip() for line in lines) if s)


@dataclass(frozen=True)
class KeyEntry:
    tag: str
    gt: tuple[str, ...]
    label: str


def _class_of(gt: tuple[str, ...], vendor: Sequence[str], upstream: Sequence[str]) -> str:
    if gt == normalize(vendor):
        return "keep_vendor"
    if gt == normalize(upstream):
        return "take_upstream"
    return "merge"


def _sides(conflict: list[str]) -> tuple[list[str], list[str]]:
    body = conflict[1:-1]
    sep = next(i for i, line in enumerate(body) if line.startswith("======="))
    return body[:sep], body[sep + 1 :]


def load_key(split: str) -> dict[str, KeyEntry]:
    """Scorable hunks only (gt is not null), keyed by tag."""
    if parse_v2(split):
        verify_hashes(v2_dir())
        raw = v2_records(split, "key")
    else:
        root = data_dir()
        verify_hashes(root)
        raw = json.loads((root / f"{SPLITS[split]}_key.json").read_text())
    out: dict[str, KeyEntry] = {}
    for r in raw:
        if r["gt"] is None:
            continue
        gt = normalize(r["gt"])
        vendor, upstream = _sides(r["conflict"])
        out[r["tag"]] = KeyEntry(r["tag"], gt, _class_of(gt, vendor, upstream))
    return out


def is_correct(resolution: Sequence[str] | None, entry: KeyEntry) -> bool:
    return resolution is not None and normalize(resolution) == entry.gt


def class_of_candidate(
    resolution: Sequence[str], vendor: Sequence[str], upstream: Sequence[str]
) -> str:
    return _class_of(normalize(resolution), vendor, upstream)


def clopper_pearson_upper(errors: int, n: int, confidence: float = 0.95) -> float:
    """One-sided upper bound on a binomial rate."""
    if n == 0:
        return 1.0
    if errors >= n:
        return 1.0
    alpha = 1.0 - confidence

    def cdf(p: float) -> float:
        return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(errors + 1))

    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if cdf(mid) > alpha:
            lo = mid
        else:
            hi = mid
    return hi


def evaluate(
    key: dict[str, KeyEntry],
    resolutions: dict[str, Sequence[str] | None],
    accepted: dict[str, bool] | None = None,
    confidence: float = 0.95,
) -> dict:
    """Accuracy per class, coverage, escaped error and certified bound over scorable hunks."""
    accepted = accepted or {}
    per_class = {c: {"n": 0, "correct": 0, "accepted": 0, "escaped": 0} for c in CLASSES}
    for tag, entry in key.items():
        ok = is_correct(resolutions.get(tag), entry)
        acc = bool(accepted.get(tag))
        pc = per_class[entry.label]
        pc["n"] += 1
        pc["correct"] += ok
        pc["accepted"] += acc
        pc["escaped"] += acc and not ok
    n = len(key)
    correct = sum(p["correct"] for p in per_class.values())
    n_acc = sum(p["accepted"] for p in per_class.values())
    escaped = sum(p["escaped"] for p in per_class.values())
    return {
        "n": n,
        "correct": correct,
        "accuracy": correct / n if n else 0.0,
        "accepted": n_acc,
        "coverage": n_acc / n if n else 0.0,
        "escaped": escaped,
        "escaped_rate": escaped / n_acc if n_acc else 0.0,
        "certified_bound": clopper_pearson_upper(escaped, n_acc, confidence),
        "per_class": per_class,
    }


def correctness(
    key: dict[str, KeyEntry], resolutions: dict[str, Sequence[str] | None]
) -> dict[str, bool]:
    return {tag: is_correct(resolutions.get(tag), e) for tag, e in key.items()}


def paired_bootstrap(
    a: dict[str, float], b: dict[str, float], resamples: int = 10000, seed: int = 0
) -> dict[str, float]:
    """Mean of (b - a) over shared hunks with a percentile 95% interval."""
    tags = sorted(set(a) & set(b))
    diffs = [b[t] - a[t] for t in tags]
    if not diffs:
        return {"delta": 0.0, "std": 0.0, "lo": 0.0, "hi": 0.0, "n": 0}
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
    mu = sum(means) / resamples
    return {
        "delta": sum(diffs) / n,
        "std": (sum((m - mu) ** 2 for m in means) / (resamples - 1)) ** 0.5,
        "lo": means[int(0.025 * resamples)],
        "hi": means[int(0.975 * resamples) - 1],
        "n": n,
    }


def leak_probes(visible_texts: Iterable[str]) -> list[tuple[str, str, str]]:
    """References that never appear in agent-visible text, as (split, tag, probe) for the audit.

    A keep-vendor reference equals text the agent is allowed to see, so only references absent
    from every visible block can reveal that a key file reached a prompt.
    """
    visible = "\n".join("\n".join(normalize(t.split("\n"))) for t in visible_texts)
    probes = []
    for split in SPLITS:
        for tag, e in load_key(split).items():
            probe = "\n".join(e.gt)
            if len(probe) >= 40 and probe not in visible:
                probes.append((split, tag, probe))
    return probes


def line_f1(resolution: Sequence[str] | None, entry: KeyEntry) -> float:
    """Dense reward: F1 over normalized lines, so a near miss scores above a wrong side."""
    if resolution is None:
        return 0.0
    got, want = list(normalize(resolution)), list(entry.gt)
    if not got and not want:
        return 1.0
    if not got or not want:
        return 0.0
    from collections import Counter

    overlap = sum((Counter(got) & Counter(want)).values())
    if not overlap:
        return 0.0
    p, r = overlap / len(got), overlap / len(want)
    return 2 * p * r / (p + r)
