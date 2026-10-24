"""Frozen copy of the delta operators as they were before the anchoring fix.

Used only by `python -m forkward operators` to report how many hunks each operator reproduced
before and after the fix. Nothing in the resolution pipeline imports this module.
"""

from __future__ import annotations

from collections.abc import Sequence

from forkward.algebra import Change
from forkward.data import Hunk


def parse_changes(diff: str) -> list[Change]:
    changes: list[Change] = []
    body: list[str] = []
    for line in diff.split("\n") + ["@@"]:
        if line.startswith(("@@", "... (")):
            changes.extend(_runs(body))
            body = []
        elif line[:1] in (" ", "+", "-") or line == "":
            body.append(line if line else " ")
    return changes


def _runs(body: list[str]) -> list[Change]:
    out: list[Change] = []
    i = 0
    while i < len(body):
        if body[i][0] == " ":
            i += 1
            continue
        start = i
        removed, added = [], []
        while i < len(body) and body[i][0] != " ":
            (removed if body[i][0] == "-" else added).append(body[i][1:])
            i += 1
        before = (body[start - 1][1:],) if start > 0 else ()
        after = (body[i][1:],) if i < len(body) else ()
        out.append(Change(before, tuple(removed), tuple(added), after))
    return out


def _find(hay: Sequence[str], needle: Sequence[str]) -> int:
    norm = [s.strip() for s in hay]
    want = [s.strip() for s in needle]
    for i in range(len(norm) - len(want) + 1):
        if norm[i : i + len(want)] == want:
            return i
    return -1


def apply_changes(target: Sequence[str], changes: list[Change]) -> tuple[str, ...] | None:
    out = list(target)
    applied = 0
    for ch in changes:
        if ch.removed:
            pos = _find(out, ch.removed)
            if pos < 0 or (ch.added and _find(out, ch.added) >= 0):
                continue
            out[pos : pos + len(ch.removed)] = ch.added
            applied += 1
        elif ch.added and _find(out, ch.added) < 0:
            if ch.before and (pos := _find(out, ch.before)) >= 0:
                out[pos + 1 : pos + 1] = ch.added
                applied += 1
            elif ch.after and (pos := _find(out, ch.after)) >= 0:
                out[pos:pos] = ch.added
                applied += 1
    return tuple(out) if applied else None


def upstream_delta_on_vendor(h: Hunk) -> tuple[str, ...] | None:
    return apply_changes(h.vendor, parse_changes(h.upstream_diff))


def vendor_delta_on_upstream(h: Hunk) -> tuple[str, ...] | None:
    return apply_changes(h.upstream, parse_changes(h.vendor_diff))
