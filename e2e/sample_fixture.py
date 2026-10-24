"""Regenerate frontend/src/lib/demo/sample.ts, the recorded sync shown on the sample page.

It clones SuryanshSS1011/luminal, merges luminal-ai/luminal at 77d3060f into its
codex/pr566-ci-extras branch exactly as Forkward did, and reads the five conflicts with the same
code a live sync uses. The votes, confidences and gate decisions are the ones recorded on that run.
Rule sources are limited to the operators that existed when it was recorded.

    uv run python -m e2e.sample_fixture [workdir]
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from forkward import algebra, legacy_algebra, sync

FORK, BRANCH = "SuryanshSS1011/luminal", "codex/pr566-ci-extras"
UPSTREAM, SHA = "luminal-ai/luminal", "77d3060f"
PERSONAS = ["vendor_first", "upstream_fix_first", "refactor_aware"]
RECORDING_OPS = {
    "keep_vendor",
    "take_upstream",
    "vendor_then_upstream",
    "upstream_then_vendor",
    "upstream_delta_on_vendor",
    "vendor_delta_on_upstream",
    "empty",
}
# What Forkward recorded per hunk: the classifier's label, each persona's vote, the winning
# source, the confidence, and whether the gate applied it.
RECORDED = {
    "crates/pytorch/utils/src/translate/attention.rs#1": (
        "merge",
        ["merge", "merge", "merge"],
        "upstream_delta_on_vendor",
        0.974,
        True,
    ),
    "crates/pytorch/utils/src/translate/ops.rs#1": (
        "merge",
        ["keep_vendor", "take_upstream", "keep_vendor"],
        "keep_vendor",
        0.631,
        False,
    ),
    "src/egglog_core/egglog_utils/base.rs#1": (
        "merge",
        ["keep_vendor"] * 3,
        "keep_vendor",
        0.971,
        False,
    ),
    "src/frontend/movement.rs#1": ("merge", ["keep_vendor"] * 3, "keep_vendor", 0.979, False),
    "src/frontend/movement.rs#2": ("merge", ["take_upstream"] * 3, "take_upstream", 0.934, False),
}
EVENTS = [
    ("fetch", "stage", f"Started: {FORK} with {UPSTREAM}@main"),
    ("fetch", "stage", f"Fetched {UPSTREAM} main, 7 new commits"),
    ("merge", "stage", f"Merged {SHA} into {BRANCH} on a new branch: 5 conflicts in 4 files"),
    ("resolve", "stage", "Resolving 5 conflict hunks"),
    ("review", "warning", "4 of 5 hunks need you: the classifier expected both sides kept"),
    (
        "review",
        "info",
        (
            "Built with cargo: 9 errors in luminal_pytorch_utils, 6 of them in files "
            "that never conflicted. All 9 were repaired and the rebuild passed."
        ),
    ),
]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def merged_worktree(work: Path) -> Path:
    fork = work / "fork"
    if not fork.exists():
        git(
            work,
            "clone",
            "-q",
            "--filter=blob:none",
            "--no-checkout",
            f"https://github.com/{FORK}.git",
            str(fork),
        )
    git(fork, "fetch", "-q", f"https://github.com/{UPSTREAM}.git", "main:refs/up/main")
    wt = work / "wt"
    if wt.exists():
        git(fork, "worktree", "remove", "--force", str(wt))
    git(fork, "worktree", "add", "-q", "--detach", str(wt), f"origin/{BRANCH}")
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=r",
            "-c",
            "user.email=r@e",
            "-c",
            "merge.conflictStyle=merge",
            "merge",
            "--no-edit",
            SHA,
        ],
        cwd=wt,
        capture_output=True,
        check=False,
    )
    return wt


def build(wt: Path) -> dict:
    hunks = []
    for i, (h, f, _) in enumerate(sync.conflict_hunks(wt, SHA)):
        cls, votes, src, conf, auto = RECORDED[h.tag]
        ops = {c.source.removeprefix("op:"): list(c.lines) for c in algebra.candidates(h)}
        if (legacy := legacy_algebra.upstream_delta_on_vendor(h)) is not None:
            ops["upstream_delta_on_vendor"] = list(legacy)
        if (legacy := legacy_algebra.vendor_delta_on_upstream(h)) is not None:
            ops["vendor_delta_on_upstream"] = list(legacy)
        ops = {k: v for k, v in ops.items() if k in RECORDING_OPS}
        by_label = {
            "keep_vendor": list(h.vendor),
            "take_upstream": list(h.upstream),
            "merge": ops.get("upstream_delta_on_vendor", []),
        }
        winner = ops[src]
        groups: dict[str, list[str]] = {}
        for p, v in zip(PERSONAS, votes, strict=True):
            groups.setdefault(v, []).append(p)
        cands, seen = [], set()
        for label in [*groups, "keep_vendor", "take_upstream"]:
            lines = by_label[label]
            if tuple(lines) in seen:
                continue
            seen.add(tuple(lines))
            rules = [k for k, v in ops.items() if v == lines]
            cands.append(
                {
                    "lines": lines,
                    "sources": rules + [f"model:{p}" for p in groups.get(label, [])],
                    "votes": len(groups.get(label, [])),
                    "label": label,
                    "chosen": lines == winner,
                }
            )
        cands.sort(key=lambda c: (not c["chosen"], -c["votes"]))
        reason = (
            "Every proposal agreed with a deterministic rule, the classifier backed it, and "
            "confidence cleared the calibrated bar"
            if auto
            else "The classifier expected both sides kept, but the proposals did not do that"
            if len(set(votes)) == 1
            else "The proposals disagreed, and the classifier expected both sides kept"
        )
        chosen = next(c for c in cands if c["chosen"])
        hunks.append(
            {
                "id": f"sample-{i + 1}",
                "file": f,
                "idx": i,
                "hunk_ref": h.tag,
                "decision": "auto" if auto else "review",
                "confidence": conf,
                "source": "+".join(chosen["sources"]),
                "reason": reason,
                "applied": auto,
                "review": None,
                "resolution": "\n".join(winner),
                "final_resolution": None,
                "review_commit": None,
                "detail": {
                    "vendor": list(h.vendor),
                    "upstream": list(h.upstream),
                    "candidates": cands,
                    "classifier": cls,
                    "proposers": len(PERSONAS),
                },
            }
        )
    ts = "2026-10-11T18:20:00Z"
    return {
        "id": "sample",
        "repo": FORK,
        "kind": "sync",
        "status": "waiting_approval",
        "stage": "review",
        "progress": {"done": len(hunks), "total": len(hunks)},
        "pr_url": None,
        "summary": {
            "hunks": len(hunks),
            "auto_applied": sum(h["applied"] for h in hunks),
            "needs_review": sum(not h["applied"] for h in hunks),
            "branch": f"forkward/sync-{SHA}",
            "clean_merge": False,
        },
        "error": None,
        "created_at": ts,
        "updated_at": ts,
        "upstream_ref": f"{UPSTREAM}@main",
        "base": BRANCH,
        "branch": f"forkward/sync-{SHA}",
        "review_requested": 0,
        "review_applied": 0,
        "hunks": hunks,
        "events": [
            {"id": i + 1, "ts": ts, "stage": s, "kind": k, "message": m, "data": {}}
            for i, (s, k, m) in enumerate(EVENTS)
        ],
    }


HEADER = """/**
 * A recorded sync, shown on the sample page with the same view as a real job: {fork}'s
 * {branch} branch merging {upstream} at {sha}. The conflicts are the real ones git produces for
 * that merge, and the votes, confidences and gate decisions are the ones Forkward recorded on
 * that run. No pull request was opened for it, so the sample has none.
 *
 * Generated by e2e/sample_fixture.py. Do not edit by hand.
 */
import type {{ JobDetail }} from "@/lib/jobs";

export const SAMPLE_JOB: JobDetail = """


def main() -> int:
    work = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp())
    work.mkdir(parents=True, exist_ok=True)
    job = build(merged_worktree(work))
    out = Path(__file__).resolve().parents[1] / "frontend/src/lib/demo/sample.ts"
    out.write_text(
        HEADER.format(fork=FORK, branch=BRANCH, upstream=UPSTREAM, sha=SHA)
        + json.dumps(job, indent=1)
        + ";\n"
    )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
