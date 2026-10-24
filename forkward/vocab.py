"""Identifier vocabulary for the lesson leakage filter.

The vocabulary is every identifier and identifier part that appears in code in any split's
blocks, test included, with comments and string literals removed. A lesson word is flagged when
it is in that vocabulary and is not an English word. English means the word, or a simple
inflection stem of it, is in english_code_words.txt, minus a denylist of code abbreviations
that happen to be dictionary words. That file is the intersection of the code vocabulary with
Webster's Second International (the public-domain `web2` list shipped by BSD systems).
Regenerate it with build_allowlist() when the benchmark gains a vendor. Words it has never seen
are flagged, which only costs a lesson, never a leak.
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

from forkward.data import SPLITS, data_dir

ALLOWLIST = Path(__file__).resolve().parent / "english_code_words.txt"
_STRIP = re.compile(r"/\*.*?\*/|//[^\n]*|\"(?:\\.|[^\"\\\n])*\"", re.DOTALL)
_FENCE = re.compile(r"```(?:diff)?\n(.*?)```", re.DOTALL)
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PARTS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+|[A-Z]+")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")


# Dictionary words that in kernel code are almost always abbreviations or identifiers.
def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


CODE_ABBREVIATIONS = _words(
    """alt amp ana ara batt bool cal cid coe con conn cos cur dar das del den dev dier dis div
    doff dom dont err errs fifo fops fut gen ist iter ked kern len lut mem mult mux nid numa para
    ped pic pix plat pol prep prob prog prop rect ree ref refs reg regs rep resp ret rev rid rot
    sai sar sec sess sig sin sip soc socs stu swa swab teri tim trig tst ull val vals vext yan
    andy lynx"""
)
# Irregular forms the suffix rules below cannot reduce to a dictionary entry.
IRREGULAR = _words(
    """has does did done using used rewritten rewrote took taken kept made chose chosen went
    gone been being was were its isn wasn doesn didn won wrote written"""
)
_SUFFIXES = (
    ("ies", "y"),
    ("es", ""),
    ("s", ""),
    ("ed", ""),
    ("ed", "e"),
    ("ing", ""),
    ("ing", "e"),
    ("ly", ""),
    ("ization", "ize"),
    ("ations", "ate"),
    ("ation", "ate"),
    ("ers", ""),
    ("er", ""),
)


def parts(token: str) -> set[str]:
    """The token and its underscore and camelCase parts, lowercase, two or more characters with
    a digit or three or more without."""
    out = {token.lower()}
    for piece in token.split("_"):
        if piece:
            out.add(piece.lower())
            out.update(p.lower() for p in _PARTS.findall(piece))
    return {t for t in out if len(t) >= 3 or (len(t) == 2 and any(c.isdigit() for c in t))}


def code_tokens(block: str) -> set[str]:
    """Identifier parts in the fenced code of one block, ignoring comments and strings."""
    code = _STRIP.sub(" ", "\n".join(_FENCE.findall(block)))
    out: set[str] = set()
    for tok in _IDENT.findall(code):
        out |= parts(tok)
    return out


@functools.lru_cache(maxsize=1)
def identifier_vocabulary(root: str | None = None) -> frozenset[str]:
    """Identifier parts from every split's blocks. Empty when the data is not available."""
    base = Path(root) if root else data_dir()
    out: set[str] = set()
    for name in SPLITS.values():
        path = base / f"{name}_blocks.json"
        if not path.exists():
            continue
        for b in json.loads(path.read_text()):
            out |= code_tokens(b["block"])
    return frozenset(out)


@functools.lru_cache(maxsize=1)
def english() -> frozenset[str]:
    if not ALLOWLIST.exists():
        return frozenset()
    return frozenset(w for w in ALLOWLIST.read_text().split() if w)


def stems(word: str) -> set[str]:
    out = {word}
    for suf, rep in _SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 2:
            out.add(word[: -len(suf)] + rep)
    if len(word) > 4 and word.endswith(("ed", "ing")):
        cut = word[:-2] if word.endswith("ed") else word[:-3]
        if len(cut) > 2 and cut[-1] == cut[-2]:
            out.add(cut[:-1])
    return out


def is_english(word: str, dictionary: frozenset[str] | None = None) -> bool:
    w = word.lower()
    if w in CODE_ABBREVIATIONS:
        return False
    if w in IRREGULAR:
        return True
    d = english() if dictionary is None else dictionary
    return any(s in d for s in stems(w))


def data_identifier(text: str) -> str | None:
    """The first word of `text` that is an identifier from the benchmark and not English."""
    vocab = identifier_vocabulary()
    for m in _WORD.finditer(text):
        w = m.group(0).lower()
        if w in vocab and (any(c.isdigit() for c in w) or not is_english(w)):
            return m.group(0)
    return None


def build_allowlist(web2: str, out: Path = ALLOWLIST) -> int:
    """Write the English words of the identifier vocabulary, using a web2 wordlist file."""
    dictionary = frozenset(w.strip().lower() for w in Path(web2).read_text().split())
    words = sorted(
        w
        for w in identifier_vocabulary()
        if not any(c.isdigit() for c in w) and any(s in dictionary for s in stems(w))
    )
    out.write_text("\n".join(words) + "\n")
    return len(words)
