"""Leakage audit, run before the single test evaluation. Prints a pass or fail table."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from forkward import algebra, score
from forkward.agents import leakage_reason
from forkward.data import load_hunks, verify_hashes
from forkward.score import normalize
from forkward.store import RUNS, read_jsonl


def _runs() -> list[Path]:
    return sorted(p for p in RUNS.iterdir() if p.is_dir()) if RUNS.exists() else []


def _norm(text: str) -> str:
    return "\n".join(normalize(text.split("\n")))


def audit() -> list[tuple[str, bool, str]]:
    rows: list[tuple[str, bool, str]] = []

    try:
        n = len(verify_hashes())
        rows.append(("1. split.json hashes match", True, f"{n} files verified"))
    except Exception as e:  # noqa: BLE001
        rows.append(("1. split.json hashes match", False, str(e)))

    attempts = [a for r in _runs() for a in read_jsonl(r, "attempts")]
    events = [e for r in _runs() for e in read_jsonl(r, "events")]
    hunks = [h for s in ("train", "validation") for h in load_hunks(s)]
    hunks += load_hunks("test", allow_test=True)
    # Operator outputs are computed from the visible block, so they count as visible text.
    visible = [h.prompt for h in hunks]
    visible += ["\n".join(c.lines) for h in hunks for c in algebra.candidates(h)]
    probes = score.leak_probes(visible)
    prompts = [_norm(a.get("prompt") or "") for a in attempts]
    gt_field = sum('"gt"' in (a.get("prompt") or "") for a in attempts)
    ev_gt = sum('"gt"' in json.dumps(e) for e in events)
    leaked = [(s, t) for s, t, p in probes if any(p in x for x in prompts)]
    kinds = Counter(a.get("kind") for a in attempts)
    rows.append(
        (
            "2. no key field or reference text in any prompt",
            # With no logged prompts there is nothing to audit, so the check fails, not passes.
            bool(attempts) and gt_field == 0 and ev_gt == 0 and not leaked,
            ("no prompts logged under runs/; nothing to audit. " if not attempts else "")
            + f"{len(attempts)} prompts ({', '.join(f'{k} {v}' for k, v in sorted(kinds.items()))}), "
            f"{len(events)} events, {len(probes)} reference probes; "
            f'"gt" in prompts {gt_field}, in events {ev_gt}, references found {len(leaked)}'
            + (f" {leaked[:5]}" if leaked else ""),
        )
    )

    frozen_path = RUNS / "frozen.json"
    frozen = json.loads(frozen_path.read_text()) if frozen_path.exists() else None
    cutoff = frozen["frozen_at"] if frozen else float("inf")
    early = [
        e
        for e in events
        if (e.get("split") == "test" or str(e.get("hunk", "")).startswith("rk"))
        and e.get("t", 0) < cutoff
    ]
    early_attempts = [
        a
        for a in attempts
        if (a.get("split") == "test" or str(a.get("tag", "")).startswith("rk"))
        and not str(a.get("run_id", "")).startswith("test-")
    ]
    rows.append(
        (
            "3. no test hunk touched before the freeze",
            not early and not early_attempts,
            (
                f"frozen at {frozen['frozen_at'] if frozen else 'not yet'}; "
                f"early test events {len(early)}, test attempts outside the test run "
                f"{len(early_attempts)}"
            ),
        )
    )

    bad_lessons = []
    n_lessons = 0
    for r in _runs():
        for pv in read_jsonl(r, "playbook_versions"):
            for lesson in pv.get("lessons", []):
                n_lessons += 1
                if why := leakage_reason(lesson["text"]):
                    bad_lessons.append((pv["version"], why))
    if frozen:
        for lesson in frozen["playbook"]["lessons"]:
            n_lessons += 1
            if why := leakage_reason(lesson["text"]):
                bad_lessons.append((frozen["playbook"]["version"], why))
    rows.append(
        (
            "4. no identifiers, paths or hex values in any lesson",
            not bad_lessons,
            f"{n_lessons} lessons checked"
            + (f"; violations {bad_lessons[:5]}" if bad_lessons else ""),
        )
    )

    test_runs = [r for r in _runs() if r.name.startswith("test-")]
    if not test_runs:
        rows.append(
            (
                "5. test run used the frozen strategy, same model, one attempt per hunk",
                frozen is not None,
                "test not run yet; frozen strategy "
                + (f"{frozen['frozen_strategy_id']} on {frozen['model']}" if frozen else "missing"),
            )
        )
    else:
        ta = [a for r in test_runs for a in read_jsonl(r, "attempts")]
        models = {a.get("model") for a in ta}
        dup = [
            k
            for k, v in Counter(
                (a.get("kind"), a.get("persona"), a.get("playbook"), a.get("tag")) for a in ta
            ).items()
            if v > 1
        ]
        feedback = [a for a in ta if a.get("kind") in ("reflect", "curate")]
        strategies = {e.get("strategy") for r in test_runs for e in read_jsonl(r, "events")} - {
            None
        }
        ok = (
            frozen is not None
            and len(test_runs) == 1
            and models == {frozen["model"]}
            and not dup
            and not feedback
            and frozen["frozen_strategy_id"] in strategies
        )
        rows.append(
            (
                "5. test run used the frozen strategy, same model, one attempt per hunk",
                ok,
                (
                    f"test runs {len(test_runs)}, models {sorted(m for m in models if m)}, "
                    f"strategies {sorted(strategies)}, duplicate attempts {len(dup)}, "
                    f"feedback calls {len(feedback)}"
                ),
            )
        )
    return rows


def render(rows: list[tuple[str, bool, str]]) -> str:
    w = max(len(r[0]) for r in rows)
    lines = [f"{'check'.ljust(w)}  result  evidence", "-" * (w + 60)]
    lines += [f"{name.ljust(w)}  {'PASS' if ok else 'FAIL'}    {ev}" for name, ok, ev in rows]
    verdict = all(ok for _, ok, _ in rows)
    lines.append(f"\nOverall: {'PASS' if verdict else 'FAIL'}")
    return "\n".join(lines)
