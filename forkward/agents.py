"""Classifier, resolver personas, reflector and curator. Prompts are built only from Hunk fields."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field

from forkward import llm, vocab
from forkward.algebra import Candidate
from forkward.data import Hunk

LABELS = ("keep_vendor", "take_upstream", "merge")

PERSONAS: dict[str, str] = {
    "zero_shot": "You resolve git merge conflicts.",
    "vendor_first": (
        "You maintain a vendor fork of the Linux kernel. You are conservative: the vendor's own "
        "code usually stays, and you take an upstream change only when it fixes a bug the vendor "
        "code still has or when the vendor code would no longer compile against upstream."
    ),
    "upstream_fix_first": (
        "You backport upstream stable fixes into vendor kernels. You make sure every upstream "
        "bug fix in the conflict survives, then restore vendor-only additions around it."
    ),
    "minimal_diff": (
        "You resolve conflicts with the smallest possible edit relative to the vendor side. "
        "Prefer resolutions that equal one side exactly when that side is already correct."
    ),
    "refactor_aware": (
        "You look for refactors: renamed helpers, moved blocks, changed signatures, and code "
        "the vendor already rewrote or relocated elsewhere. A block the vendor moved should not "
        "be reintroduced, and an upstream rename must be applied to vendor code that uses it."
    ),
    "operator_picker": (
        "You are given candidate resolutions produced by deterministic merge operators. Pick the "
        "one the vendor's engineers would commit, or write a better one if none is right."
    ),
}

_INSTRUCTIONS = """Resolve ONLY the conflict block named in the hunk header. Output the exact lines that \
replace the whole block from <<<<<<< through >>>>>>> (markers removed), keeping the file's \
indentation. If the correct resolution is to delete the block entirely, output an empty block.

Answer in this format:
REASON: <one or two sentences>
LESSONS: <comma separated ids of playbook lessons you used, or none>
```resolution
<resolved lines>
```"""

_FENCE = re.compile(r"```resolution[^\n]*\n(.*?)```", re.DOTALL)
_ANY_FENCE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)


@dataclass
class Lesson:
    id: str
    text: str
    helpful: int = 0
    harmful: int = 0
    round: int = 0


@dataclass
class Playbook:
    version: str
    parent: str | None
    lessons: list[Lesson] = field(default_factory=list)

    def render(self) -> str:
        if not self.lessons:
            return ""
        lines = [f"[{x.id}] {x.text}" for x in self.lessons]
        return "Playbook of lessons learned from earlier merges:\n" + "\n".join(lines)

    def to_doc(self) -> dict:
        return {
            "version": self.version,
            "parent": self.parent,
            "lessons": [asdict(x) for x in self.lessons],
        }

    @classmethod
    def from_doc(cls, d: dict) -> Playbook:
        return cls(d["version"], d.get("parent"), [Lesson(**x) for x in d.get("lessons", [])])


EMPTY_PLAYBOOK = Playbook("pb-0", None, [])


@dataclass
class Proposal:
    persona: str
    lines: tuple[str, ...] | None
    lessons_used: tuple[str, ...]
    completion: llm.Completion
    reason: str = ""
    prompt: str = ""


def _sides_text(h: Hunk) -> str:
    return (
        "Vendor side of the conflict (between <<<<<<< and =======):\n```\n"
        + "\n".join(h.vendor)
        + "\n```\nUpstream side of the conflict (between ======= and >>>>>>>):\n```\n"
        + "\n".join(h.upstream)
        + "\n```"
    )


def parse_resolution(text: str) -> tuple[str, ...] | None:
    m = _FENCE.findall(text) or _ANY_FENCE.findall(text)
    if not m:
        return None
    body = m[-1]
    body = body.removesuffix("\n")
    lines = tuple(body.split("\n")) if body else ()
    if any(x.startswith(("<<<<<<<", ">>>>>>>", "=======")) for x in lines):
        return None
    return lines


def _lessons_used(text: str, playbook: Playbook) -> tuple[str, ...]:
    m = re.search(r"^LESSONS:(.*)$", text, re.MULTILINE)
    if not m:
        return ()
    ids = {x.id for x in playbook.lessons}
    return tuple(t for t in re.findall(r"[A-Za-z]+-?\d+", m.group(1)) if t in ids)


ABSTAIN_INSTRUCTIONS = """If you cannot tell which resolution the vendor's engineers would commit, \
answer with the single line ABSTAIN instead of a resolution block. An abstention is sent to a \
person; a wrong resolution may be merged."""


def build_messages(
    h: Hunk,
    persona: str,
    playbook: Playbook,
    operator_candidates: list[Candidate] | None = None,
    hint: str = "",
    abstain: bool = False,
) -> list[dict[str, str]]:
    """The exact chat messages a resolver persona sees. Training data is exported with this same
    function, so a fine-tuned model is served the prompt it was trained on."""
    parts = [h.prompt, _sides_text(h)]
    if persona == "operator_picker" and operator_candidates:
        opts = "\n\n".join(
            f"Candidate {i + 1} ({c.source}):\n```\n" + "\n".join(c.lines) + "\n```"
            for i, c in enumerate(operator_candidates)
        )
        parts.append(opts)
    pb = playbook.render() if persona != "zero_shot" else ""
    if pb:
        parts.append(pb)
    if hint and persona != "zero_shot":
        parts.append(hint)
    parts.append(_INSTRUCTIONS)
    if abstain:
        parts.append(ABSTAIN_INSTRUCTIONS)
    return [
        {"role": "system", "content": PERSONAS[persona]},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


async def propose(
    h: Hunk,
    persona: str,
    playbook: Playbook,
    operator_candidates: list[Candidate] | None = None,
    temperature: float = 0.2,
    hint: str = "",
    seed: int | None = None,
) -> Proposal:
    messages = build_messages(h, persona, playbook, operator_candidates, hint)
    user = messages[1]["content"]
    c = await llm.complete(
        messages,
        role="generator",
        temperature=temperature,
        max_tokens=8192,
        want_logprobs=True,
        seed=seed,
    )
    reason = ""
    if m := re.search(r"^REASON:(.*)$", c.text, re.MULTILINE):
        reason = m.group(1).strip()[:300]
    lines = parse_resolution(c.text) if c.ok else None
    return Proposal(persona, lines, _lessons_used(c.text, playbook), c, reason, user)


_JUDGE = """Two candidate resolutions for the same conflict block are below. Decide which one the \
vendor's engineers committed. Consider whether an upstream bug fix must survive, whether the vendor \
removed or moved this code, and whether both sides' additions belong. Answer with one letter on the \
last line: A or B."""


async def judge(
    h: Hunk, a: tuple[str, ...], b: tuple[str, ...]
) -> tuple[str | None, llm.Completion, str]:
    user = "\n\n".join(
        [
            h.prompt,
            _sides_text(h),
            "Candidate A:\n```\n" + "\n".join(a) + "\n```",
            "Candidate B:\n```\n" + "\n".join(b) + "\n```",
            _JUDGE,
        ]
    )
    c = await llm.complete(
        [
            {"role": "system", "content": "You review kernel merge conflict resolutions."},
            {"role": "user", "content": user},
        ],
        role="generator",
        temperature=0.0,
        seed=0,
        max_tokens=1024,
    )
    found = (
        re.findall(r"\b([AB])\b", c.text.strip().splitlines()[-1])
        if c.ok and c.text.strip()
        else []
    )
    return (found[-1] if found else None), c, user


async def classify(h: Hunk, playbook: Playbook) -> tuple[str | None, llm.Completion, str]:
    prompt = "\n\n".join(
        x
        for x in [
            h.prompt,
            _sides_text(h),
            playbook.render(),
            (
                "Predict how the vendor's engineers resolved this conflict. Answer with exactly one "
                "word on the last line: keep_vendor (the vendor side unchanged), take_upstream (the "
                "upstream side unchanged), or merge (anything else)."
            ),
        ]
        if x
    )
    c = await llm.complete(
        [
            {"role": "system", "content": "You classify kernel merge conflict resolutions."},
            {"role": "user", "content": prompt},
        ],
        role="generator",
        temperature=0.0,
        seed=0,
        max_tokens=2048,
    )
    if not c.ok:
        return None, c, prompt
    found = re.findall(r"keep_vendor|take_upstream|merge", c.text)
    return (found[-1] if found else None), c, prompt


_REFLECT = """You are improving a playbook used by agents that resolve merge conflicts when a \
vendor's Linux kernel fork pulls in upstream stable releases. Below are training hunks the \
current strategy got wrong. For each, you see the conflict, the chosen resolution, and which \
proposed candidates the scorer marked correct or wrong. You never see the reference text.

Write up to {k} short, general lessons (one sentence each) that would help on OTHER conflicts \
from OTHER vendors. Lessons must be about conventions and decision rules, for example when \
vendors keep their own side or when an upstream fix must be carried into vendor code.

Strict rules: never mention a file name, path, function, macro, variable, register, hex value, \
version number, or vendor name. Never use underscores or camelCase. Write in plain English.

Answer as a JSON list of strings and nothing else."""


async def reflect(failures: list[dict], k: int = 4) -> tuple[list[str], llm.Completion, str]:
    body = []
    for f in failures[:8]:
        cands = "\n".join(
            f"- {src}: "
            + ("CORRECT" if v is True or v >= 1 else f"wrong (line overlap {float(v):.2f})")
            for src, v in f["candidate_outcomes"].items()
        )
        body.append(
            f"{f['prompt'][:6000]}\n\nChosen resolution ({f['chosen_source']}), scored WRONG:\n```\n"
            + "\n".join(f["chosen"] or ["<no answer>"])
            + f"\n```\nCandidate outcomes:\n{cands}"
        )
    user = "\n\n---\n\n".join(body)
    c = await llm.complete(
        [
            {"role": "system", "content": _REFLECT.format(k=k)},
            {"role": "user", "content": user},
        ],
        role="reflector",
        temperature=0.4,
        seed=0,
        max_tokens=4096,
    )
    return (_json_list(c.text) if c.ok else []), c, user


_CURATE = """You curate a playbook of at most {cap} lessons for agents that resolve vendor kernel \
merge conflicts. Merge the new lessons into the current playbook: drop duplicates, merge \
near-duplicates, keep lessons with good helpful counts, and drop lessons with high harmful \
counts. Keep each lesson to one plain-English sentence with no identifiers, file names, \
underscores, camelCase or hex values.

Answer as a JSON list of objects {{"text": ..., "from": [ids of current lessons it keeps or \
merges, or empty for a new lesson]}} and nothing else."""


async def curate(
    playbook: Playbook, new_lessons: list[str], round_no: int, cap: int
) -> tuple[Playbook, list[dict], llm.Completion | None, str]:
    """Returns the child playbook, the rejected lessons with reasons, the curator's completion
    and the prompt it was sent (empty when no call was needed), so the prompt can be audited."""
    rejected: list[dict] = []
    clean_new = []
    for t in new_lessons:
        if why := leakage_reason(t):
            rejected.append({"text": t, "reason": why})
        else:
            clean_new.append(t)
    if not clean_new:
        kept = list(playbook.lessons)
        pb = Playbook(playbook_id(playbook.version, kept), playbook.version, kept)
        return pb, rejected, None, ""
    current = "\n".join(
        f"[{x.id}] (helpful {x.helpful}, harmful {x.harmful}) {x.text}" for x in playbook.lessons
    )
    user = f"Current playbook:\n{current or '(empty)'}\n\nNew lessons:\n" + "\n".join(
        f"- {t}" for t in clean_new
    )
    c = await llm.complete(
        [
            {"role": "system", "content": _CURATE.format(cap=cap)},
            {"role": "user", "content": user},
        ],
        role="curator",
        temperature=0.0,
        seed=0,
        max_tokens=4096,
    )
    by_id = {x.id: x for x in playbook.lessons}
    merged: list[Lesson] = []
    items = _json_list(c.text) if c.ok else []
    if not items:
        items = [{"text": x.text, "from": [x.id]} for x in playbook.lessons] + [
            {"text": t, "from": []} for t in clean_new
        ]
    seen: set[str] = set()
    for it in items:
        text = (it.get("text") if isinstance(it, dict) else str(it)) or ""
        text = text.strip()
        key = re.sub(r"\W+", " ", text.lower()).strip()
        if not text or key in seen:
            continue
        if why := leakage_reason(text):
            rejected.append({"text": text, "reason": why})
            continue
        seen.add(key)
        srcs = [by_id[i] for i in (it.get("from") or []) if isinstance(it, dict) and i in by_id]
        if len(srcs) == 1 and srcs[0].text == text:
            merged.append(Lesson(srcs[0].id, text, srcs[0].helpful, srcs[0].harmful, srcs[0].round))
        else:
            merged.append(
                Lesson(
                    lesson_id(text),
                    text,
                    sum(s.helpful for s in srcs),
                    sum(s.harmful for s in srcs),
                    round_no,
                )
            )
    merged.sort(key=lambda x: x.helpful - x.harmful, reverse=True)
    kept = merged[:cap]
    return Playbook(playbook_id(playbook.version, kept), playbook.version, kept), rejected, c, user


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def lesson_id(text: str) -> str:
    """Digits only, so the resolvers' LESSONS line parses back to the same id."""
    return f"L{int(_digest(text.strip().lower()), 16) % 10**6:06d}"


def playbook_id(parent: str | None, lessons: list[Lesson]) -> str:
    """A content hash of the parent and the lesson texts, so a rerun that writes the same
    playbook gets the same version and hits the same proposal cache."""
    return "pb-" + _digest(parent or "", *(x.text for x in lessons))[:8]


_PATH = re.compile(r"\b[\w.-]+/[\w./-]+|\b\w+\.(?:c|h|dts|dtsi|S|py|rs|mk|json|yaml|txt)\b")
_IDENT = re.compile(r"\b\w*_\w+\b|\b[a-z]+[A-Z]\w*\b|\b[A-Z][a-z]+[A-Z]\w*\b")
_HEX = re.compile(r"\b0x[0-9a-fA-F]+\b|\b[0-9a-f]{7,40}\b")
_CALL = re.compile(r"\b[A-Za-z]\w*\s*\(\s*\)|\b[a-z]\w*\(")
_VERSION = re.compile(r"\b\d+\.\d+(?:\.\d+)?\b|\bv\d")
_VENDORS = re.compile(
    r"\b(?:nxp|imx|i\.mx|stm32|stmicro\w*|st\b|rockchip|rk\d+|raspberry)", re.IGNORECASE
)


def leakage_reason(text: str) -> str | None:
    for name, rx in (
        ("file path", _PATH),
        ("identifier", _IDENT),
        ("hex value", _HEX),
        ("function name", _CALL),
        ("version", _VERSION),
        ("vendor name", _VENDORS),
    ):
        if m := rx.search(text):
            return f"{name}: {m.group(0)!r}"
    if word := vocab.data_identifier(text):
        return f"identifier from the benchmark code: {word!r}"
    return None


def _json_list(text: str) -> list:
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        v = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return v if isinstance(v, list) else []
