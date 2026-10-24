"""Training data and a verifiable reward for fine-tuning a small open model as a resolver.

`export` writes chat-format JSONL from the benchmark v2 dev pool only; the holdout pool is never
read. Every row carries the vendor's reference for the reward, the hunk's class, its vendor fold,
its hash half and its merge, so a trainer can hold a vendor out and resample by merge. The prompts
come from `agents.build_messages`, the function the live resolver uses, so a trained model is
served exactly the prompt it saw in training.

`reward` is the graded reward for GRPO. It is defined here, next to the scorer, so training and
evaluation can never drift apart:

    1.0          the resolution is correct (normalized-line match, the benchmark's definition)
    0.3 * F1     partial credit by line F1 against the reference, to densify the signal
    r_abstain    for answering ABSTAIN (default 0.0; sweep it)
    -0.1         for no parsable resolution block

Run `python -m forkward export-training --out training/` to write sft/rl/eval files.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from forkward import agents, score
from forkward.data import hunk_meta, load_hunks, v2_half
from forkward.score import KeyEntry, normalize

ABSTAIN = "ABSTAIN"
FOLDS = ("nxp", "st", "rk")


def reward(
    completion: str, reference: list[str] | tuple[str, ...], r_abstain: float = 0.0
) -> float:
    """Graded reward for one sampled completion against the vendor's reference lines."""
    text = completion.strip()
    if text == ABSTAIN or text.splitlines()[-1:] == [ABSTAIN]:
        return r_abstain
    lines = agents.parse_resolution(completion)
    if lines is None:
        return -0.1
    entry = KeyEntry("", normalize(reference), "")
    if score.is_correct(lines, entry):
        return 1.0
    return 0.3 * score.line_f1(lines, entry)


def _row(h, e: KeyEntry, meta: dict, abstain: bool) -> dict:
    messages = agents.build_messages(h, "zero_shot", agents.EMPTY_PLAYBOOK, abstain=abstain)
    return {
        "tag": h.tag,
        "messages": messages,
        "reference": list(e.gt),
        "class": e.label,
        "fold": meta.get("vendor"),
        "half": v2_half(h.tag),
        "merge": meta.get("merge"),
    }


def _sft_target(e: KeyEntry) -> str:
    body = "\n".join(e.gt)
    return f"REASON: matches the vendor's committed resolution.\nLESSONS: none\n```resolution\n{body}\n```"


def export(out: Path, eval_fold: str = "st", abstain: bool = True) -> dict:
    """Write rl.jsonl and sft.jsonl from every dev fold except `eval_fold`, and eval.jsonl from
    `eval_fold`, which is for checkpoint selection only."""
    if eval_fold not in FOLDS:
        raise ValueError(f"eval_fold must be one of {FOLDS}")
    out.mkdir(parents=True, exist_ok=True)
    counts: dict[str, Counter] = {}
    files = {"rl": [], "sft": [], "eval": []}
    for fold in FOLDS:
        split = f"dev:{fold}"
        key = score.load_key(split)
        meta = hunk_meta(split)
        for h in load_hunks(split):
            if h.tag not in key:
                continue
            row = _row(h, key[h.tag], meta[h.tag], abstain)
            if fold == eval_fold:
                files["eval"].append(row)
                continue
            files["rl"].append(row)
            sft = {
                **row,
                "messages": row["messages"]
                + [{"role": "assistant", "content": _sft_target(key[h.tag])}],
            }
            files["sft"].append(sft)
    for name, rows in files.items():
        with (out / f"{name}.jsonl").open("w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        counts[name] = Counter(r["class"] for r in rows)
    manifest = {
        "source": "benchmark v2 dev pool (heldout_v2 at 16d96dd); the holdout pool is not read",
        "eval_fold": eval_fold,
        "abstain_instruction": abstain,
        "counts": {k: {"n": sum(v.values()), **v} for k, v in counts.items()},
        "reward": "1.0 correct, 0.3 * line F1 otherwise, r_abstain for ABSTAIN, -0.1 unparsable",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def main(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="forkward export-training")
    p.add_argument("--out", default="training")
    p.add_argument("--eval-fold", default="st", choices=FOLDS)
    p.add_argument("--no-abstain", action="store_true")
    a = p.parse_args(argv)
    m = export(Path(a.out), a.eval_fold, not a.no_abstain)
    print(json.dumps(m, indent=1))
    return 0
