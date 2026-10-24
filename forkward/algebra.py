"""Deterministic resolution operators. No model calls.

Line-level operators take whole sides, concatenate them, or carry one side's diff onto the other.
A delta operator anchors every change on all of its context lines, needs a unique match inside
the conflict, and returns None rather than a partial result. The merged-file excerpt around the
conflict is part of the search buffer, so context that lies just outside the block still anchors.
A diff the block format cut short ("... (N more diff lines omitted)") can hide changes, so a
candidate built from one is kept but never supplies the gate's operator vote.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from forkward.data import Hunk, context

_TRUNCATED = re.compile(r"^\.\.\. \(")


@dataclass(frozen=True)
class Candidate:
    lines: tuple[str, ...]
    source: str
    deterministic: bool
    # False when the operator saw incomplete input, so the gate cannot count it as an operator vote.
    vote: bool = True


@dataclass(frozen=True)
class Change:
    before: tuple[str, ...]
    removed: tuple[str, ...]
    added: tuple[str, ...]
    after: tuple[str, ...]

    def reversed(self) -> Change:
        return Change(self.before, self.added, self.removed, self.after)


Context = tuple[tuple[str, ...], tuple[str, ...]]
NO_CONTEXT: Context = ((), ())


def diff_truncated(diff: str) -> bool:
    return any(_TRUNCATED.match(line) for line in diff.split("\n"))


def parse_changes(diff: str) -> list[Change]:
    """Split a unified diff into runs of removed and added lines. Each run keeps every context
    line between it and the neighbouring runs of its @@ section. A run cut off by truncation is
    dropped, because its removed or added lines may be incomplete."""
    changes: list[Change] = []
    body: list[str] = []
    for line in diff.split("\n") + ["@@"]:
        if line.startswith("@@") or _TRUNCATED.match(line):
            runs = _runs(body)
            if _TRUNCATED.match(line) and runs and body and body[-1][0] != " ":
                runs = runs[:-1]
            changes.extend(runs)
            body = []
        elif line[:1] in (" ", "+", "-") or line == "":
            body.append(line if line else " ")
    return changes


def _runs(body: list[str]) -> list[Change]:
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(body):
        if body[i][0] == " ":
            i += 1
            continue
        start = i
        while i < len(body) and body[i][0] != " ":
            i += 1
        spans.append((start, i))
    out = []
    for k, (s, e) in enumerate(spans):
        lo = spans[k - 1][1] if k else 0
        hi = spans[k + 1][0] if k + 1 < len(spans) else len(body)
        out.append(
            Change(
                tuple(x[1:] for x in body[lo:s]),
                tuple(x[1:] for x in body[s:e] if x[0] == "-"),
                tuple(x[1:] for x in body[s:e] if x[0] == "+"),
                tuple(x[1:] for x in body[e:hi]),
            )
        )
    return out


def _eq(a: str, b: str) -> bool:
    return a.strip() == b.strip()


def _context_ok(buf: Sequence[str], s: int, n: int, before, after) -> int | None:
    """Matched non-blank context lines around buf[s:s+n], or None on any mismatch. Context that
    runs past either end of buf is clipped, since the excerpt simply ends there."""
    anchors = 0
    for k in range(1, len(before) + 1):
        if s - k < 0:
            break
        if not _eq(buf[s - k], before[-k]):
            return None
        anchors += bool(before[-k].strip())
    for k, want in enumerate(after):
        if s + n + k >= len(buf):
            break
        if not _eq(buf[s + n + k], want):
            return None
        anchors += bool(want.strip())
    return anchors


def locate(buf: Sequence[str], core: Sequence[str], before, after, lo: int, hi: int) -> list[int]:
    """Every start s where `core` fills buf[s:s+len(core)] inside [lo, hi) and all context lines
    that fall inside buf match. A pure insertion also needs one matched non-blank context line."""
    n = len(core)
    hits = []
    for s in range(lo, hi - n + 1):
        if not all(_eq(buf[s + k], core[k]) for k in range(n)):
            continue
        anchors = _context_ok(buf, s, n, before, after)
        if anchors is not None and (n or anchors):
            hits.append(s)
    return hits


def _one_sided(buf: Sequence[str], core, before, after, lo: int, hi: int) -> list[int]:
    """Positions anchored by the context on one side only, each side needing one non-blank match
    and a unique hit. Used by the fuzzy operators when the other side edited adjacent lines."""
    found = set()
    for b, a in ((before, ()), ((), after)):
        if not any(x.strip() for x in b + a):
            continue
        hits = [s for s in locate(buf, core, b, a, lo, hi) if _context_ok(buf, s, len(core), b, a)]
        # _context_ok counts matched non-blank lines, so a hit anchored only by clipping is out.
        if len(hits) == 1:
            found.add(hits[0])
    return sorted(found)


def _find_unique(buf, core, ch: Change, lo: int, hi: int, fuzzy: bool) -> list[int]:
    hits = locate(buf, core, ch.before, ch.after, lo, hi)
    if fuzzy and len(hits) != 1:
        hits = _one_sided(buf, core, ch.before, ch.after, lo, hi)
    return hits


def apply_changes(
    target: Sequence[str], changes: list[Change], ctx: Context = NO_CONTEXT, fuzzy: bool = False
) -> tuple[str, ...] | None:
    """Apply every change to `target`, or return None.

    Each change must match exactly once inside target (its context may extend into `ctx`), or
    be already present at its anchored location. Any other outcome fails the whole operator.
    With `fuzzy`, a change that has no unique full-context match may anchor on the context of
    one side alone, still uniquely.
    """
    cb, ca = ctx
    buf = [*cb, *target, *ca]
    lo, hi = len(cb), len(cb) + len(target)
    edits = []
    for ch in changes:
        pre = _find_unique(buf, ch.removed, ch, lo, hi, fuzzy)
        if len(pre) == 1:
            edits.append((pre[0], len(ch.removed), ch.added))
        elif pre or len(_find_unique(buf, ch.added, ch, lo, hi, fuzzy)) != 1:
            return None
    if not edits or not _disjoint(edits):
        return None
    for s, n, added in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        buf[s : s + n] = added
    return tuple(buf[lo : len(buf) - len(ca)])


def _disjoint(edits: list[tuple[int, int, tuple[str, ...]]]) -> bool:
    edits = sorted(edits, key=lambda e: (e[0], e[1]))
    for (s1, n1, _), (s2, n2, _) in itertools.pairwise(edits):
        if s1 + n1 > s2 or (s1 == s2 and n1 == n2 == 0):
            return False
    return True


def relevant(
    source: Sequence[str], changes: list[Change], ctx: Context = NO_CONTEXT
) -> list[Change]:
    """Changes whose result is visible inside `source`, so they belong to this conflict."""
    cb, ca = ctx
    buf = [*cb, *source, *ca]
    lo, hi = len(cb), len(cb) + len(source)
    return [ch for ch in changes if locate(buf, ch.added, ch.before, ch.after, lo, hi)]


def apply_delta(
    source: Sequence[str],
    target: Sequence[str],
    changes: list[Change],
    ctx: Context = NO_CONTEXT,
    fuzzy: bool = False,
) -> tuple[str, ...] | None:
    """Carry the changes that produced `source` onto `target`, all of them or none."""
    rel = relevant(source, changes, ctx)
    return apply_changes(target, rel, ctx, fuzzy) if rel else None


def keep_vendor(h: Hunk) -> tuple[str, ...]:
    return h.vendor


def take_upstream(h: Hunk) -> tuple[str, ...]:
    return h.upstream


def vendor_then_upstream(h: Hunk) -> tuple[str, ...]:
    return h.vendor + h.upstream


def upstream_then_vendor(h: Hunk) -> tuple[str, ...]:
    return h.upstream + h.vendor


def upstream_delta_on_vendor(h: Hunk) -> tuple[str, ...] | None:
    return apply_delta(h.upstream, h.vendor, parse_changes(h.upstream_diff), context(h))


def vendor_delta_on_upstream(h: Hunk) -> tuple[str, ...] | None:
    return apply_delta(h.vendor, h.upstream, parse_changes(h.vendor_diff), context(h))


def upstream_delta_on_vendor_fuzzy(h: Hunk) -> tuple[str, ...] | None:
    out = apply_delta(h.upstream, h.vendor, parse_changes(h.upstream_diff), context(h), True)
    return None if out == upstream_delta_on_vendor(h) else out


def vendor_delta_on_upstream_fuzzy(h: Hunk) -> tuple[str, ...] | None:
    out = apply_delta(h.vendor, h.upstream, parse_changes(h.vendor_diff), context(h), True)
    return None if out == vendor_delta_on_upstream(h) else out


def empty(h: Hunk) -> tuple[str, ...]:
    return ()


OPERATORS: dict[str, Callable[[Hunk], tuple[str, ...] | None]] = {
    "keep_vendor": keep_vendor,
    "take_upstream": take_upstream,
    "vendor_then_upstream": vendor_then_upstream,
    "upstream_then_vendor": upstream_then_vendor,
    "upstream_delta_on_vendor": upstream_delta_on_vendor,
    "vendor_delta_on_upstream": vendor_delta_on_upstream,
    "empty": empty,
}
# One-sided context anchoring. These only add a candidate when the strict operator found none,
# and never supply the gate's operator vote.
FUZZY = {
    "upstream_delta_on_vendor_fuzzy": upstream_delta_on_vendor_fuzzy,
    "vendor_delta_on_upstream_fuzzy": vendor_delta_on_upstream_fuzzy,
}
# Operators that read a diff, and which one. A truncated diff takes away their vote.
DIFF_INPUTS = {
    "upstream_delta_on_vendor": ("upstream_diff",),
    "vendor_delta_on_upstream": ("vendor_diff",),
}


def complete_input(h: Hunk, name: str) -> bool:
    return not any(diff_truncated(getattr(h, f)) for f in DIFF_INPUTS.get(name, ()))


def candidates(h: Hunk, token: bool = False, fuzzy: bool = False) -> list[Candidate]:
    """Line-level operator candidates, plus the fuzzy delta operators and the token-level set
    when asked. Only strict operators with complete input may vote."""
    out = []
    for name, op in OPERATORS.items():
        lines = op(h)
        if lines is not None:
            out.append(Candidate(tuple(lines), f"op:{name}", True, complete_input(h, name)))
    for name, op in FUZZY.items() if fuzzy else ():
        if (lines := op(h)) is not None:
            out.append(Candidate(tuple(lines), f"op:{name}", True, False))
    if token:
        from forkward import token_algebra

        out += token_algebra.candidates(h)
    return out
