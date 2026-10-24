"""Hints from similar training conflicts: which kind of resolution won, never the resolved text.

The resolver sees the class of the vendor's resolution on the most similar training hunks and
which deterministic operator reproduced it. That is scorer feedback on train, the same signal the
reflector receives; reference lines never enter a prompt. A training hunk never retrieves itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from forkward import algebra, score
from forkward.data import Hunk, load_hunks

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_WORDS = {
    "keep_vendor": "kept the vendor side",
    "take_upstream": "took the upstream side",
    "merge": "merged both sides by hand",
}


@dataclass(frozen=True)
class Entry:
    tag: str
    ext: str
    tokens: frozenset[str]
    vendor_lines: int
    upstream_lines: int
    label: str
    operators: tuple[str, ...]


def _tokens(h: Hunk) -> frozenset[str]:
    return frozenset(_TOKEN.findall("\n".join(h.vendor + h.upstream)))


class Index:
    def __init__(self) -> None:
        key = score.load_key("train")
        self.entries: list[Entry] = []
        for h in load_hunks("train"):
            if h.tag not in key:
                continue
            e = key[h.tag]
            ops = tuple(
                c.source.removeprefix("op:")
                for c in algebra.candidates(h)
                if score.is_correct(c.lines, e)
            )
            self.entries.append(
                Entry(
                    h.tag,
                    PurePosixPath(h.file).suffix or "none",
                    _tokens(h),
                    len(score.normalize(h.vendor)),
                    len(score.normalize(h.upstream)),
                    e.label,
                    ops,
                )
            )

    def similar(self, h: Hunk, k: int = 3) -> list[tuple[float, Entry]]:
        toks = _tokens(h)
        ext = PurePosixPath(h.file).suffix or "none"
        scored = []
        for e in self.entries:
            if e.tag == h.tag:
                continue
            union = len(toks | e.tokens) or 1
            sim = len(toks & e.tokens) / union + (0.15 if e.ext == ext else 0.0)
            scored.append((sim, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:k]

    def hint(self, h: Hunk, k: int = 3) -> str:
        rows = []
        for sim, e in self.similar(h, k):
            how = _WORDS[e.label]
            op = (
                f"; the {e.operators[0].replace('_', ' ')} operator reproduced it"
                if e.operators
                else ""
            )
            rows.append(
                f"- a {e.ext} conflict with {e.vendor_lines} vendor and {e.upstream_lines} upstream lines "
                f"(similarity {sim:.2f}): the vendor's engineers {how}{op}"
            )
        if not rows:
            return ""
        return "How the most similar conflicts from other merges were resolved:\n" + "\n".join(rows)


_INDEX: Index | None = None


def index() -> Index:
    global _INDEX
    if _INDEX is None:
        _INDEX = Index()
    return _INDEX
