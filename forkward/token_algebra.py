"""Token-level operators, after MergeBERT's primitive merge patterns (Svyatkovskiy et al., 2022).

A line-level conflict often hides edits that do not touch at token granularity, such as a
vendor rename and an upstream constant fix on the same line. These operators rebuild the base
of the conflict region by reverting each side's own diff, compute token edits from base to each
side, and apply every edit that does not overlap an edit from the other side. Regions where the
two sides' edits overlap are resolved with one primitive pattern applied to every region. The
patterns are take vendor, take upstream, keep base, vendor then upstream, and upstream then
vendor. With no overlapping region all patterns agree and a single interleave candidate is made.

A candidate may vote in the gate only when both sides' diffs are complete and both reverted
sides give the same base.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from forkward import algebra
from forkward.algebra import Candidate
from forkward.data import Hunk, context
from forkward.score import normalize

_TOKEN = re.compile(r"\n|[^\S\n]+|\w+|[^\w\s]")
PATTERNS = ("vendor", "upstream", "base", "vendor_then_upstream", "upstream_then_vendor")
NAMES = ("interleave", *PATTERNS)

Edit = tuple[int, int, tuple[str, ...]]


def tokenize(lines: Sequence[str]) -> list[str]:
    return _TOKEN.findall("\n".join(lines))


def detokenize(tokens: Sequence[str]) -> tuple[str, ...]:
    return tuple("".join(tokens).split("\n")) if tokens else ()


@dataclass(frozen=True)
class Base:
    lines: tuple[str, ...]
    # True when both sides were reverted from complete diffs and agreed on the base.
    confirmed: bool


def reconstruct_base(h: Hunk) -> Base | None:
    """The conflict region as it was before either side changed it, or None when unknown."""
    ctx = context(h)
    guesses = []
    for side, diff in ((h.upstream, h.upstream_diff), (h.vendor, h.vendor_diff)):
        rel = algebra.relevant(side, algebra.parse_changes(diff), ctx)
        if rel and (b := algebra.apply_changes(side, [c.reversed() for c in rel], ctx)) is not None:
            guesses.append((b, not algebra.diff_truncated(diff)))
    if not guesses:
        return None
    if len(guesses) == 2 and normalize(guesses[0][0]) != normalize(guesses[1][0]):
        return None
    confirmed = len(guesses) == 2 and all(complete for _, complete in guesses)
    return Base(guesses[0][0], confirmed)


def edits(base: Sequence[str], side: Sequence[str]) -> list[Edit]:
    """Token edits that turn base into side, as (start, end, replacement) on base positions."""
    sm = difflib.SequenceMatcher(None, base, side, autojunk=False)
    return [
        (i1, i2, tuple(side[j1:j2])) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"
    ]


@dataclass
class Region:
    lo: int
    hi: int
    vendor: list[Edit] = field(default_factory=list)
    upstream: list[Edit] = field(default_factory=list)

    @property
    def conflict(self) -> bool:
        return bool(self.vendor and self.upstream) and self.vendor != self.upstream


def regions(ev: list[Edit], eu: list[Edit]) -> list[Region]:
    """Group edits whose closed base ranges touch or overlap, as diff3 does at line level."""
    items = sorted([(e, "vendor") for e in ev] + [(e, "upstream") for e in eu], key=lambda x: x[0])
    out: list[Region] = []
    for (i1, i2, rep), side in items:
        if not out or i1 > out[-1].hi:
            out.append(Region(i1, i2))
        r = out[-1]
        r.hi = max(r.hi, i2)
        getattr(r, side).append((i1, i2, rep))
    return out


def _apply(base: Sequence[str], lo: int, hi: int, es: list[Edit]) -> list[str]:
    out, pos = [], lo
    for i1, i2, rep in sorted(es):
        out += base[pos:i1]
        out += rep
        pos = i2
    return out + list(base[pos:hi])


def _region_text(base: Sequence[str], r: Region, pattern: str) -> list[str]:
    if not r.conflict:
        return _apply(base, r.lo, r.hi, r.vendor or r.upstream)
    v = _apply(base, r.lo, r.hi, r.vendor)
    u = _apply(base, r.lo, r.hi, r.upstream)
    return {
        "vendor": v,
        "upstream": u,
        "base": list(base[r.lo : r.hi]),
        "vendor_then_upstream": v + u,
        "upstream_then_vendor": u + v,
    }[pattern]


def merge(base: Sequence[str], vendor: Sequence[str], upstream: Sequence[str]) -> dict:
    """Token-level three-way merge. Returns {pattern: lines} for each primitive pattern, or
    {"interleave": lines} when no region conflicts."""
    tb, tv, tu = tokenize(base), tokenize(vendor), tokenize(upstream)
    rs = regions(edits(tb, tv), edits(tb, tu))
    patterns = PATTERNS if any(r.conflict for r in rs) else ("interleave",)
    out = {}
    for p in patterns:
        toks, pos = [], 0
        for r in rs:
            toks += tb[pos : r.lo]
            toks += _region_text(tb, r, p)
            pos = r.hi
        out[p] = detokenize(toks + tb[pos:])
    return out


def candidates(h: Hunk) -> list[Candidate]:
    base = reconstruct_base(h)
    if base is None:
        return []
    return [
        Candidate(lines, f"op:tok_{name}", True, base.confirmed)
        for name, lines in merge(base.lines, h.vendor, h.upstream).items()
    ]
