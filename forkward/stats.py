"""Paired comparisons for small benchmarks: exact McNemar, Holm, Wilson and a cluster bootstrap.

Per-hunk outcomes are dicts {tag: 0.0 or 1.0}. Every comparison reports the discordant counts,
because with a few dozen hunks the exact test on those counts is the honest summary, and the
percentile bootstrap is shown next to it rather than instead of it.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from statistics import NormalDist

from forkward.score import paired_bootstrap


def binom_tail_ge(k: int, n: int, p: float = 0.5) -> float:
    """P(X >= k) for X ~ Binomial(n, p)."""
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k, n + 1))


def mcnemar(a: dict[str, float], b: dict[str, float]) -> dict:
    """Exact McNemar test on hunks scored by both arms.

    `a_only` counts hunks only arm a got right and `b_only` hunks only arm b got right. The
    two-sided p doubles the smaller binomial tail. `p_b_better` is the one-sided p for the
    alternative that b is more accurate than a.
    """
    tags = sorted(set(a) & set(b))
    a_only = sum(1 for t in tags if a[t] >= 0.5 > b[t])
    b_only = sum(1 for t in tags if b[t] >= 0.5 > a[t])
    d = a_only + b_only
    two = min(1.0, 2 * binom_tail_ge(max(a_only, b_only), d)) if d else 1.0
    return {
        "n": len(tags),
        "a_only": a_only,
        "b_only": b_only,
        "discordant": d,
        "p_two_sided": two,
        "p_b_better": binom_tail_ge(b_only, d) if d else 1.0,
    }


def holm(pvals: dict[str, float]) -> dict[str, float]:
    """Holm step-down adjusted p-values, controlling the family-wise error rate."""
    order = sorted(pvals, key=pvals.get)
    m = len(order)
    out, running = {}, 0.0
    for i, name in enumerate(order):
        running = max(running, min(1.0, (m - i) * pvals[name]))
        out[name] = running
    return out


def wilson(k: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Two-sided Wilson score interval for a binomial proportion."""
    if n == 0:
        return 0.0, 1.0
    z = NormalDist().inv_cdf(1 - (1 - confidence) / 2)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def cluster_bootstrap(
    a: dict[str, float],
    b: dict[str, float],
    clusters: dict[str, str],
    resamples: int = 10000,
    seed: int = 0,
) -> dict:
    """Mean of (b - a) with a percentile 95% interval that resamples whole clusters.

    Hunks from one merge or one file are not independent, so resampling clusters instead of
    hunks gives an interval that reflects how many independent units the benchmark really has.
    """
    groups: dict[str, list[float]] = defaultdict(list)
    for t in sorted(set(a) & set(b)):
        groups[clusters.get(t, t)].append(b[t] - a[t])
    if not groups:
        return {"delta": 0.0, "lo": 0.0, "hi": 0.0, "std": 0.0, "n": 0, "clusters": 0}
    units = [(sum(v), len(v)) for _, v in sorted(groups.items())]
    rng = random.Random(seed)
    k = len(units)
    means = []
    for _ in range(resamples):
        pick = [units[rng.randrange(k)] for _ in range(k)]
        means.append(sum(s for s, _ in pick) / sum(n for _, n in pick))
    means.sort()
    mu = sum(means) / resamples
    n = sum(n for _, n in units)
    return {
        "delta": sum(s for s, _ in units) / n,
        "lo": means[int(0.025 * resamples)],
        "hi": means[int(0.975 * resamples) - 1],
        "std": (sum((m - mu) ** 2 for m in means) / max(1, resamples - 1)) ** 0.5,
        "n": n,
        "clusters": k,
    }


def compare(
    a: dict[str, float],
    b: dict[str, float],
    clusters: dict[str, dict[str, str]] | None = None,
    resamples: int = 10000,
    seed: int = 0,
) -> dict:
    """Arm b against arm a: delta, exact McNemar, hunk bootstrap, and one cluster bootstrap per
    clustering in `clusters` (for example {"merge": {...}, "file": {...}})."""
    tags = sorted(set(a) & set(b))
    out = {
        "n": len(tags),
        "a_correct": int(sum(a[t] for t in tags)),
        "b_correct": int(sum(b[t] for t in tags)),
        "mcnemar": mcnemar(a, b),
        "bootstrap": paired_bootstrap(a, b, resamples, seed),
        "cluster_bootstrap": {
            name: cluster_bootstrap(a, b, cl, resamples, seed)
            for name, cl in (clusters or {}).items()
        },
    }
    # Top-level delta, lo, hi and std keep the shape older readers (the board) expect.
    out.update({k: out["bootstrap"][k] for k in ("delta", "lo", "hi", "std")})
    out["a_wilson"] = wilson(out["a_correct"], len(tags))
    out["b_wilson"] = wilson(out["b_correct"], len(tags))
    return out


def adjust(comparisons: dict[str, dict | None]) -> dict[str, dict | None]:
    """Add Holm-adjusted two-sided McNemar p-values across a family of comparisons."""
    live = {k: v["mcnemar"]["p_two_sided"] for k, v in comparisons.items() if v}
    adj = holm(live)
    for k, v in comparisons.items():
        if v:
            v["mcnemar"]["p_holm"] = adj[k]
    return comparisons


def render(name: str, c: dict | None, width: int = 34) -> str:
    """One line: delta, discordant counts, exact p (and Holm p when set), and every interval."""
    if not c:
        return f"{name:<{width}} not available"
    m, bs = c["mcnemar"], c["bootstrap"]
    holm_p = f" holm p={m['p_holm']:.3f}" if "p_holm" in m else ""
    parts = [
        f"{name:<{width}} {c['delta'] * 100:+5.1f} pts",
        f"discordant {m['a_only']} vs {m['b_only']}",
        f"exact p={m['p_two_sided']:.3f}{holm_p}",
        f"hunk CI [{bs['lo'] * 100:+.1f}, {bs['hi'] * 100:+.1f}]",
    ]
    for by, cb in c.get("cluster_bootstrap", {}).items():
        parts.append(f"{by} CI ({cb['clusters']}) [{cb['lo'] * 100:+.1f}, {cb['hi'] * 100:+.1f}]")
    return "  ".join(parts)


def clusters_for(split: str) -> dict[str, dict[str, str]]:
    """Merge and file clusters for a split, from the agent-visible blocks."""
    from forkward.data import hunk_meta

    meta = hunk_meta(split)
    return {
        "merge": {t: m["merge"] or t for t, m in meta.items()},
        "file": {t: m["file"] or t for t, m in meta.items()},
    }
