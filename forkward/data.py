"""Load agent-visible hunks from the frozen benchmark. Never reads key files."""

from __future__ import annotations

import functools
import hashlib
import json
import os
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SPLITS = {"train": "nxp", "validation": "st", "test": "rk"}
# Benchmark v2 (heldout_v2, frozen at 16d96dd): vendor-held-out pools. A v2 split name is
# "dev", "dev:<fold>", "dev:<fold>:<half>" or "dev::<half>", with fold one of nxp, st, rk and half
# A or B, or "holdout" for the frozen test pool (Xilinx, Android Common Kernel, Variscite).
V2_POOLS = {"dev", "holdout"}
V2_FOLDS = ("nxp", "st", "rk")

_CONFLICT_RE = re.compile(r"^Conflict lines (\d+) to (\d+) of the merged file\.$", re.MULTILINE)
_EXCERPT_RE = re.compile(r"^Merged file excerpt \(lines (\d+) to (\d+)\)", re.MULTILINE)


@dataclass(frozen=True)
class Hunk:
    tag: str
    split: str
    file: str
    prompt: str
    vendor: tuple[str, ...]
    upstream: tuple[str, ...]
    upstream_diff: str
    vendor_diff: str


class DataError(RuntimeError):
    pass


def data_dir() -> Path:
    d = os.environ.get("DATA_DIR") or str(Path(__file__).resolve().parents[2] / "forkward-data")
    return Path(d)


def v2_dir() -> Path:
    d = os.environ.get("DATA_V2_DIR") or str(data_dir().parent / "heldout_v2")
    return Path(d)


def parse_v2(split: str) -> tuple[str, str | None, str | None] | None:
    """(pool, fold, half) for a v2 split name, or None for a v1 split."""
    parts = split.split(":")
    if parts[0] not in V2_POOLS or len(parts) > 3:
        return None
    pool, fold, half = parts[0], (parts[1:2] or [None])[0] or None, (parts[2:3] or [None])[0]
    if pool == "holdout" and (fold or half):
        raise ValueError("the v2 holdout pool has no folds or halves")
    if fold is not None and fold not in V2_FOLDS:
        raise ValueError(f"unknown v2 fold {fold!r}; choose one of {V2_FOLDS}")
    if half is not None and half not in ("A", "B"):
        raise ValueError("v2 half must be A or B")
    return pool, fold, half


def v2_records(split: str, kind: str, root: Path | None = None) -> list[dict]:
    """Rows of `<series>_<kind>.json` that belong to a v2 split. `kind` is blocks or key; only
    score.py asks for key rows."""
    pool, fold, half = parse_v2(split)
    root = root or v2_dir()
    meta = json.loads((root / "split.json").read_text())
    out = []
    # Series names are listed per pool; reading only the pool's files keeps holdout blocks
    # unopened in every dev run.
    for series in meta["pools"][pool]["series"]:
        path = root / f"{series}_{kind}.json"
        if not path.exists():
            continue
        for r in json.loads(path.read_text()):
            if r.get("split") != pool:
                continue
            if fold and r.get("fold") != fold:
                continue
            if half and v2_half(r["tag"]) != half:
                continue
            out.append(r)
    return out


def v2_half(tag: str) -> str:
    """The split rule's half: A when SHA-256 of the tag is even, B when odd."""
    return "A" if int(hashlib.sha256(tag.encode()).hexdigest(), 16) % 2 == 0 else "B"


def verify_hashes(root: Path | None = None) -> dict[str, bool]:
    root = root or data_dir()
    split = json.loads((root / "split.json").read_text())
    result = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
        for name, digest in split["sha256"].items()
    }
    bad = [n for n, ok in result.items() if not ok]
    if bad:
        raise DataError(f"split.json hash mismatch: {bad}")
    return result


def _fences(text: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    cur: list[str] | None = None
    for line in text.split("\n"):
        if cur is None:
            if line.startswith("```"):
                cur = []
        elif line == "```":
            blocks.append(cur)
            cur = None
        else:
            cur.append(line)
    return blocks


def _split_conflict(lines: list[str]) -> tuple[list[str], list[str]]:
    if not lines or not lines[0].startswith("<<<<<<<") or not lines[-1].startswith(">>>>>>>"):
        raise DataError("conflict markers not found at expected lines")
    body = lines[1:-1]
    sep = [i for i, line in enumerate(body) if line.startswith("=======")]
    if len(sep) != 1:
        raise DataError(f"expected one separator, found {len(sep)}")
    return body[: sep[0]], body[sep[0] + 1 :]


def parse_block(tag: str, split: str, text: str) -> Hunk:
    file_m = re.search(r"^File: (.+)$", text, re.MULTILINE)
    conf_m = _CONFLICT_RE.search(text)
    exc_m = _EXCERPT_RE.search(text)
    if not (file_m and conf_m and exc_m):
        raise DataError(f"{tag}: missing header")
    fences = _fences(text)
    if len(fences) < 3:
        raise DataError(f"{tag}: expected 3 fenced sections, found {len(fences)}")
    excerpt, upstream_diff, vendor_diff = fences[0], fences[1], fences[2]
    start, a, b = int(exc_m.group(1)), int(conf_m.group(1)), int(conf_m.group(2))
    vendor, upstream = _split_conflict(excerpt[a - start : b - start + 1])
    return Hunk(
        tag=tag,
        split=split,
        file=file_m.group(1).strip(),
        prompt=text,
        vendor=tuple(vendor),
        upstream=tuple(upstream),
        upstream_diff="\n".join(upstream_diff),
        vendor_diff="\n".join(vendor_diff),
    )


@functools.lru_cache(maxsize=4096)
def context(h: Hunk) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Merged-file lines just before and just after the conflict block, from the visible excerpt.

    Empty when the prompt has no excerpt header, as for hand-built hunks in tests.
    """
    conf_m = _CONFLICT_RE.search(h.prompt)
    exc_m = _EXCERPT_RE.search(h.prompt)
    fences = _fences(h.prompt)
    if not (conf_m and exc_m and fences):
        return (), ()
    excerpt = fences[0]
    start, a, b = int(exc_m.group(1)), int(conf_m.group(1)), int(conf_m.group(2))
    return tuple(excerpt[: a - start]), tuple(excerpt[b - start + 1 :])


def hunk_meta(split: str, root: Path | None = None) -> dict[str, dict]:
    """Merge id, date and file of every hunk in a split, for cluster-level statistics and the
    seeded halves.

    Reads only the agent-visible blocks file, never a key file.
    """
    if parse_v2(split):
        raw = v2_records(split, "blocks", root)
    else:
        root = root or data_dir()
        raw = json.loads((root / f"{SPLITS[split]}_blocks.json").read_text())
    out = {}
    for r in raw:
        file_m = re.search(r"^File: (.+)$", r["block"], re.MULTILINE)
        out[r["tag"]] = {
            # ST blocks carry no merge id, but their tags name the release step (r1-h03).
            "merge": r.get("merge") or r["tag"].split("-")[0],
            "date": r.get("date") or "",
            "vendor": r.get("fold") or r.get("vendor") or "",
            "file": file_m.group(1).strip() if file_m else "",
        }
    return out


def halves(split: str, seed: int = 0, root: Path | None = None) -> tuple[frozenset, frozenset]:
    """Two disjoint, seeded halves of a split, one for selection and one for calibration.

    Hunks are grouped by file, so near-duplicate conflicts in one file never straddle the two
    halves. Files are shuffled by the seed, then placed largest first into the smaller half.
    """
    by_file: dict[str, list[str]] = {}
    for tag, m in sorted(hunk_meta(split, root).items()):
        by_file.setdefault(m["file"] or tag, []).append(tag)
    files = sorted(by_file)
    random.Random(seed).shuffle(files)
    a: list[str] = []
    b: list[str] = []
    for f in sorted(files, key=lambda f: -len(by_file[f])):
        (a if len(a) <= len(b) else b).extend(by_file[f])
    return frozenset(a), frozenset(b)


def conflict_key(h: Hunk) -> str:
    """The same conflict, whatever repository or file it appears in: both sides, normalized."""
    from forkward.score import normalize  # score imports this module

    raw = json.dumps([normalize(h.vendor), normalize(h.upstream)])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def drop_dev_repeats(holdout: list[Hunk], root: Path | None = None) -> list[Hunk]:
    """Holdout hunks whose conflict also occurs in the dev pool, dropped and counted on stderr.
    Variscite derives from NXP's kernels and resolutions repeat within a vendor family, so a model
    trained on dev references could pass those by memorization."""
    dev = {conflict_key(h) for h in load_hunks("dev", root=root)}
    kept = [h for h in holdout if conflict_key(h) not in dev]
    if len(kept) < len(holdout):
        print(
            f"[data] dropped {len(holdout) - len(kept)} of {len(holdout)} holdout hunks whose "
            "conflict also occurs in the dev pool",
            file=sys.stderr,
        )
    return kept


def load_hunks(split: str, *, allow_test: bool = False, root: Path | None = None) -> list[Hunk]:
    v2 = parse_v2(split)
    if v2:
        if v2[0] == "holdout" and not allow_test:
            raise PermissionError(
                "v2 holdout blocks are only loaded by a pre-registered evaluation"
            )
        verify_hashes(root or v2_dir())
        hunks = [
            parse_block(r["tag"], split, r["block"]) for r in v2_records(split, "blocks", root)
        ]
        return drop_dev_repeats(hunks, root) if v2[0] == "holdout" else hunks
    if split not in SPLITS:
        raise ValueError(f"unknown split {split!r}")
    if split == "test" and not allow_test:
        raise PermissionError("test blocks are only loaded by the frozen single test evaluation")
    root = root or data_dir()
    verify_hashes(root)
    raw = json.loads((root / f"{SPLITS[split]}_blocks.json").read_text())
    return [parse_block(r["tag"], split, r["block"]) for r in raw]
