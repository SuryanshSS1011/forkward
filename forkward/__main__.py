"""python -m forkward <command>"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter

from forkward import analysis, audit, billing, explore, llm, repair, schedule, score, search, sync
from forkward.data import load_hunks, verify_hashes
from forkward.store import RUNS


def check(include_test: bool = False) -> int:
    """Checkpoint 1: hashes, class counts and the always-keep-vendor baseline. The spent test
    split is read only with --test; the v2 holdout pool is never read here."""
    verify_hashes()
    print("split.json hashes: OK")
    splits = ["train", "validation"] + (["test"] if include_test else [])
    from forkward.data import v2_dir

    if (v2_dir() / "split.json").exists():
        verify_hashes(v2_dir())
        print("v2 split.json hashes: OK")
        splits += ["dev:nxp", "dev:st", "dev:rk"]
    for split in splits:
        key = score.load_key(split)
        hunks = load_hunks(split, allow_test=split == "test")
        kv = score.evaluate(key, {h.tag: h.vendor for h in hunks})
        classes = Counter(e.label for e in key.values())
        print(
            f"{split:<11} scorable {len(key):>4}  keep vendor {classes['keep_vendor']:>2}  "
            f"take upstream {classes['take_upstream']:>2}  merge {classes['merge']:>2}  "
            f"always-keep-vendor {kv['correct']}/{kv['n']} ({kv['accuracy']:.1%})"
        )
    return 0


def ping() -> int:
    """One real call to confirm the provider, model id and logprob support."""

    async def go() -> llm.Completion:
        c = await llm.complete(
            [{"role": "user", "content": "Reply with the single word ready."}],
            max_tokens=400,
            want_logprobs=True,
        )
        await llm.close()
        return c

    llm.load_env()
    c = asyncio.run(go())
    print(
        json.dumps(
            {
                "provider": c.provider,
                "model": c.model,
                "ok": c.ok,
                "text": c.text,
                "error": c.error,
                "latency_s": round(c.latency_s, 2),
                "logprobs": c.mean_logprob is not None,
                "reportable": llm.reportable(),
            },
            indent=1,
        )
    )
    return 0 if c.ok else 1


def models(argv: list[str]) -> int:
    """`models` lists and prompts; `models set <spec>` or `models set default` saves directly."""

    async def go() -> list[dict]:
        try:
            return await llm.available_models()
        finally:
            await llm.close()

    llm.load_env()
    if argv[:1] == ["set"] and len(argv) == 2:
        llm.save_default(None if argv[1] == "default" else argv[1])
        print(f"default model: {llm.saved_default() or 'from .env'}")
        return 0
    found = asyncio.run(go())
    current = llm.saved_default()
    print(f"default model: {current or 'from .env'}\n")
    for i, m in enumerate(found, 1):
        mark = "*" if m["spec"] == current else " "
        print(f"{mark} {i:>2}. {m['spec']}")
    if not any(m["provider"] == "local" for m in found):
        print("     (no local server answered on LOCAL_BASE_URL)")
    if not sys.stdin.isatty() or not found:
        return 0
    pick = input("\nNumber to make default (Enter to keep): ").strip()
    if pick.isdigit() and 1 <= int(pick) <= len(found):
        llm.save_default(found[int(pick) - 1]["spec"])
        print(f"default model: {llm.saved_default()}")
    return 0


def main() -> int:
    if "--model" in sys.argv:
        i = sys.argv.index("--model")
        spec = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
        llm.parse_spec(spec)
        os.environ["LLM_MODEL"] = spec
        del sys.argv[i : i + 2]
    p = argparse.ArgumentParser(prog="forkward")
    sub = p.add_subparsers(dest="cmd", required=True)
    ck = sub.add_parser("check", help="verify data and the keep-vendor baseline (Checkpoint 1)")
    ck.add_argument("--test", action="store_true", help="also read the spent v1 test split")
    sub.add_parser("ping", help="one live model call")
    s = sub.add_parser("search", help="round 0 baseline plus learning rounds")
    s.add_argument("--rounds", type=int, default=None)
    s.add_argument("--objective", choices=("accuracy", "coverage"), default="accuracy")
    s.add_argument("--run-id", default=None)
    f = sub.add_parser("freeze", help="freeze the final strategy of a search run")
    f.add_argument("run_id")
    sub.add_parser("audit", help="leakage audit before the test run")
    sub.add_parser("test", help="the single test evaluation of the frozen strategy")
    sub.add_parser("repair", help="fix a clean sync that no longer builds", add_help=False)
    sub.add_parser("sync", help="merge upstream into a fork and resolve it", add_help=False)
    sub.add_parser("explore", help="continue learning from a frozen run with UCB tree search")
    sub.add_parser(
        "scheduled", help="run every configured scheduled sync once (the timer calls this)"
    )
    sub.add_parser(
        "report",
        help="regenerate results/ from data and logged runs and write PROVENANCE.json "
        "(--strict fails when an input is missing)",
    )
    sub.add_parser("bundle-runs", help="tarball of runs/ with a SHA-256 manifest", add_help=False)
    sub.add_parser("ladder", help="attribute gains to each component from logged runs")
    sub.add_parser(
        "experiment",
        help="run a control: compute-matched or no-reflector (model calls; train and validation)",
        add_help=False,
    )
    sub.add_parser("selector", help="H0: non-LLM operator selector on train and validation")
    sub.add_parser(
        "operators", help="operator and oracle coverage on train and validation, before and after"
    )
    sub.add_parser("models", help="list cloud and local models and pick the default")
    sub.add_parser("calibrate", help="calibrate the gate for the configured model on validation")
    sub.add_parser("keys", help="create, list or revoke hosted API keys", add_help=False)
    sub.add_parser("db", help="apply database migrations or show their status", add_help=False)
    sub.add_parser("worker", help="run the durable sync worker (DBOS) for the hosted service")
    if len(sys.argv) > 1 and sys.argv[1] == "repair":
        return repair.main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "sync":
        return sync.main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "explore":
        return explore.main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "learn":
        from forkward import history

        res = history.record_outcome(sys.argv[2]) if len(sys.argv) > 2 else history.learn_pending()
        print(json.dumps(res, indent=1))
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "scheduled":
        return schedule.main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        return analysis.report_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "bundle-runs":
        from forkward import repro

        return repro.main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "ladder":
        return analysis.main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "experiment":
        from forkward import experiments

        llm.load_env()
        return experiments.main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "selector":
        from forkward import selector

        return selector.main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "export-training":
        from forkward import training

        return training.main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "selector-v2":
        from forkward import selector

        return selector.main_v2(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "operators":
        from forkward import oracle

        return oracle.main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "models":
        return models(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "calibrate":
        print(json.dumps(asyncio.run(sync.calibrate_current_model()), indent=1))
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "keys":
        return billing.main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        from forkward import workflows

        return workflows.worker()
    if len(sys.argv) > 1 and sys.argv[1] == "db":
        from forkward import db

        llm.load_env()
        return db.main(sys.argv[2:])
    a = p.parse_args()

    if a.cmd == "check":
        return check(a.test)
    if a.cmd == "ping":
        return ping()
    if a.cmd == "search":
        llm.load_env()
        rounds = a.rounds if a.rounds is not None else search.env_int("LEARNING_ROUNDS", 5)
        out = asyncio.run(search.search(rounds, a.objective, a.run_id))
        print(f"run written to {out}")
        return 0
    if a.cmd == "freeze":
        frozen = search.freeze(RUNS / a.run_id)
        print(f"frozen strategy {frozen['frozen_strategy_id']} from run {a.run_id}")
        return 0
    if a.cmd == "audit":
        rows = audit.audit()
        print(audit.render(rows))
        return 0 if all(ok for _, ok, _ in rows[:4]) else 1
    if a.cmd == "test":
        rows = audit.audit()
        print(audit.render(rows))
        if not all(ok for _, ok, _ in rows):
            print("audit failed: not running the test evaluation")
            return 1
        out = asyncio.run(search.evaluate_test())
        print((out / "results.json").read_text())
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
