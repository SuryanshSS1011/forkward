"""Agent observability board. Serves index.html and /api/state from the run logs.

python dashboard/server.py [--port 8765] [--run RUN_ID]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
RUNS = ROOT.parent / "runs"
_cache: dict[Path, tuple[int, list[dict]]] = {}


def read(path: Path) -> list[dict]:
    """Incrementally parse a JSONL file that only grows."""
    if not path.exists():
        return []
    size = path.stat().st_size
    offset, rows = _cache.get(path, (0, []))
    if size < offset:
        offset, rows = 0, []
    if size > offset:
        with path.open("rb") as f:
            f.seek(offset)
            chunk = f.read(size - offset)
        end = chunk.rfind(b"\n") + 1
        for line in chunk[:end].splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        offset += end
        _cache[path] = (offset, rows)
    return rows


PRIVATE_PREFIX = "private-"
RUN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,200}")


def runs() -> list[str]:
    """Public runs only. Private runs name a customer's repository and are never listed."""
    if not RUNS.exists():
        return []
    return sorted(
        (
            p.name
            for p in RUNS.iterdir()
            if p.is_dir()
            and not p.name.startswith(PRIVATE_PREFIX)
            and (p / "events.jsonl").exists()
        ),
        key=lambda n: (RUNS / n / "events.jsonl").stat().st_mtime,
    )


def checked_run(name: str | None, may_read_private: bool) -> str | None:
    """A run name from a query string, accepted only if it is a real run directory. Private runs
    need the preview secret or the admin token. Anything else, including paths, is refused."""
    if not name or name == "latest-sync":
        return name
    if not RUN_RE.fullmatch(name) or not (RUNS / name / "events.jsonl").is_file():
        raise PermissionError("unknown run")
    if name.startswith(PRIVATE_PREFIX) and not may_read_private:
        raise PermissionError("unknown run")
    return name


def admin_token() -> str:
    from forkward import llm

    llm.load_env()
    return os.environ.get("BOARD_ADMIN_TOKEN", "")


def is_admin(headers) -> bool:
    """The operator's token, sent as `Authorization: Bearer <BOARD_ADMIN_TOKEN>`."""
    token = admin_token()
    auth = headers.get("Authorization", "") or ""
    given = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    return bool(token and given and hmac.compare_digest(given.encode(), token.encode()))


def is_web_server(headers) -> bool:
    expected = preview_secret()
    given = headers.get("X-Forkward-Preview") or ""
    return bool(expected and given and hmac.compare_digest(given.encode(), expected.encode()))


def locked_down() -> bool:
    """The public deployment. Locally, with no admin token configured, the board stays open."""
    from forkward import sandbox

    return bool(os.environ.get("BOARD_READONLY") or sandbox.hosted() or admin_token())


def nines(bound: float | None) -> float | None:
    if bound is None or bound <= 0 or bound >= 1:
        return None
    return round(-math.log10(bound), 3)


JOB: dict = {"running": False, "started": 0.0, "repo": None, "upstream": None, "error": None}
JOB_COOLDOWN_S = 90


def preview_secret() -> str:
    """Shared with the web app, which loads the same .env: only the signed-in web server may ask
    the board to preview a visitor's own fork."""
    from forkward import llm

    llm.load_env()
    seed = os.environ.get("PREVIEW_SECRET") or os.environ.get("DATABASE_URL") or ""
    return hashlib.sha256(f"forkward-preview:{seed}".encode()).hexdigest() if seed else ""


def requested_target(q: dict, secret: str | None) -> dict | None:
    """A fork the web app resolved for a user signed in with GitHub, or None for the demo target."""
    repo, upstream = q.get("repo", [""])[0], q.get("upstream", [""])[0]
    expected = preview_secret()
    if not (repo and upstream and expected and secret and hmac.compare_digest(secret, expected)):
        return None
    from forkward import sync

    try:
        sync.validate_repo(repo)
        sync.validate_upstream(upstream)
    except ValueError:
        return None
    if "@" not in upstream:
        return None
    return {"repo": repo, "upstream": upstream, "base": "HEAD"}


def demo_target() -> dict | None:
    """The repository the live demo syncs: the schedule entry marked demo, else the first one.
    Anonymous visitors never choose the repository, so a public button cannot point Forkward
    elsewhere; a signed-in user's own fork arrives only through requested_target."""
    from forkward import schedule

    entries = schedule.entries()
    return next((e for e in entries if e.get("demo")), entries[0] if entries else None)


def start_preview(target: dict | None = None) -> dict:
    import threading
    import time as _t

    from forkward import sync as _sync

    private = target is not None
    target = target or demo_target()
    if not target:
        return {"error": "no repository configured in the schedule"}
    # A visitor's own fork is a private run that only the web app can read back; the configured
    # demo repository stays public.
    run = _sync.run_id("preview", private=private)
    out = {"repo": target["repo"], "upstream": target["upstream"], "run": run}
    if JOB["running"] or _t.time() - JOB["started"] < JOB_COOLDOWN_S:
        return {**out, "started": False, "note": "a sync is already running or just ran"}

    def run() -> None:
        from forkward import sync

        JOB.update(running=True, started=_t.time(), error=None)
        try:
            asyncio.run(
                sync.preview(
                    target["repo"], target.get("base") or "HEAD", target["upstream"], run=run
                )
            )
        except Exception as e:  # noqa: BLE001 - the error is shown to the console, not raised
            JOB["error"] = str(e)
        finally:
            JOB["running"] = False

    JOB.update(started=_t.time())
    threading.Thread(target=run, daemon=True).start()
    return {**out, "started": True}


def latest_run(prefixes: tuple[str, ...] | None = None) -> str | None:
    rs = [r for r in runs() if not r.startswith("test-")]
    if prefixes:
        rs = [r for r in rs if r.startswith(prefixes)]
    return rs[-1] if rs else None


def event_tail(since: float, run: str | None) -> list[dict]:
    run = run or latest_run()
    if not run:
        return []
    keep = (
        "agent",
        "kind",
        "t",
        "task",
        "hunk",
        "level",
        "message",
        "decision",
        "url",
        "source",
        "error",
    )
    rows = [e for e in read(RUNS / run / "events.jsonl") if e.get("t", 0) > since]
    return [{k: e[k] for k in keep if k in e} | {"run": run} for e in rows[-300:]]


def record_approval(decision: str, who: str = "operator") -> dict:
    import time as _t

    run = latest_run(("preview-", "sync-", "scheduled-"))
    if not run:
        return {"ok": False, "error": "no sync run to approve"}
    row = {
        "agent": "person",
        "kind": "alert",
        "level": "info",
        "t": _t.time(),
        "message": f"the {who} chose to {decision} the hunks that needed review",
        "decision": decision,
        "by": who,
    }
    with (RUNS / run / "events.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")
    return {"ok": True, "run": run, "decision": decision}


def state(run: str | None) -> dict:
    all_runs = runs()
    search_runs = [r for r in all_runs if not r.startswith("test-")]
    if run == "latest-sync":
        run = latest_run(("preview-", "sync-", "scheduled-"))
    run = run or (search_runs[-1] if search_runs else None)
    if run is None:
        return {"runs": all_runs, "run": None}
    d = RUNS / run
    events = read(d / "events.jsonl")
    lanes: dict[str, dict] = {}
    for e in events:
        a = e.get("agent")
        if not a or a in ("controller", "gate"):
            continue
        lane = lanes.setdefault(a, {"agent": a, "calls": 0, "errors": 0, "tokens": 0, "lat": 0.0})
        if e["kind"] == "start":
            lane.update(status="working", task=e.get("task"), hunk=e.get("hunk"), since=e["t"])
        elif e["kind"] in ("done", "error"):
            lane["calls"] += 1
            lane["errors"] += e["kind"] == "error"
            lane["tokens"] += e.get("tokens") or 0
            lane["lat"] += e.get("latency_s") or 0
            lane.update(
                status="idle" if e["kind"] == "done" else "error",
                task=e.get("task"),
                hunk=e.get("hunk"),
                latency_s=e.get("latency_s"),
                last_tokens=e.get("tokens"),
                decision=e.get("decision"),
                error=e.get("error"),
                at=e["t"],
            )
    for lane in lanes.values():
        lane["mean_latency_s"] = (
            round(lane.pop("lat") / lane["calls"], 2) if lane["calls"] else None
        )

    rounds = read(d / "rounds.jsonl")
    # A version is logged once when curated and again after its train run scores the lessons;
    # keep one node per version, in first-seen order, with the latest counters.
    pbs = list({p["version"]: p for p in read(d / "playbook_versions.jsonl")}.values())
    ls = d / "lesson_stats.json"
    if ls.exists():
        # Records computed from logged calls (forkward report) replace the stored counters, which
        # runs made before the counting fix left at zero for last-round lessons.
        rec = json.loads(ls.read_text())
        pbs = [
            {
                **pv,
                "lessons": [
                    {**x, "cited": 0, "helpful": 0, "harmful": 0, **rec.get(x.get("id"), {})}
                    for x in pv.get("lessons", [])
                ],
            }
            for pv in pbs
        ]
    evals = read(d / "evals.jsonl")
    final = next((x for x in reversed(evals) if x.get("kind") == "final_validation"), None)
    disagreements = [e for e in events if e.get("kind") == "disagreement"][-30:]
    alerts = [e for e in events if e.get("kind") in ("alert",)][-40:]
    decisions = [e for e in events if e.get("agent") == "gate" and e.get("kind") == "decision"]

    # Held-out results belong only to the run whose strategy was frozen, or to the test run itself.
    frozen_run = None
    if (RUNS / "frozen.json").exists():
        frozen_run = json.loads((RUNS / "frozen.json").read_text()).get("run_id")
    owns_test = run == frozen_run or run.startswith("test-")
    test = None
    for tr in [r for r in all_runs if r.startswith("test-") and owns_test][-1:]:
        res = RUNS / tr / "results.json"
        tev = read(RUNS / tr / "events.jsonl")
        alerts += [e for e in tev if e.get("kind") in ("alert", "needs_review")][-40:]
        if res.exists():
            test = json.loads(res.read_text())

    safety = None
    if final:
        v = final["strategy"]["validation"]
        cal = v.get("calibration", {})
        safety = {
            "split": "validation",
            "coverage": v.get("coverage"),
            "accepted": v.get("accepted"),
            "escaped": v.get("escaped"),
            "bound": cal.get("bound"),
            "threshold": cal.get("threshold"),
            "target_met": cal.get("target_met"),
            "grid": cal.get("grid", []),
        }
    if test and owns_test:
        f = test["frozen"]
        safety = {
            "split": "test",
            "coverage": f["coverage"],
            "accepted": f["accepted"],
            "escaped": f["escaped"],
            "bound": f["certified_bound"],
            "threshold": test["thresholds"]["frozen"],
            "target_met": None,
            "grid": (safety or {}).get("grid", []),
        }
    if safety:
        safety["nines"] = nines(safety["bound"])
        for g in safety["grid"]:
            g["nines"] = nines(g["bound"])

    import re as _re
    import time as _time

    done = [
        e
        for e in events
        if e.get("kind") in ("done", "error") and e.get("agent") not in ("controller", "gate")
    ]
    errs: dict = {}
    for e in done:
        if e["kind"] != "error":
            continue
        m = _re.match(
            r"(HTTP \d+|truncated|rate limited|connection error|refused)", e.get("error") or ""
        )
        k = m.group(1) if m else (e.get("error") or e.get("decision") or "error")[:40]
        errs.setdefault(k, {"count": 0, "agents": {}})
        errs[k]["count"] += 1
        errs[k]["agents"][e["agent"]] = errs[k]["agents"].get(e["agent"], 0) + 1
    ts = [e["t"] for e in events if "t" in e]
    last = max(ts) if ts else 0
    evals_all = read(d / "evals.jsonl")
    has_final = (
        (d / "final.json").exists()
        or any(x.get("kind") in ("final_validation", "test", "repair", "sync") for x in evals_all)
        or (d / "results.json").exists()
    )
    status = (
        "finished" if has_final else ("running" if _time.time() - last < 180 else "stopped early")
    )
    summary_ = {
        "status": status,
        "calls": len(done),
        "errors": sum(v["count"] for v in errs.values()),
        "error_kinds": errs,
        "tokens": sum(e.get("tokens") or 0 for e in done),
        "started": min(ts) if ts else None,
        "last_event": last or None,
        "rounds_completed": len(rounds),
        "kind": "test"
        if run.startswith("test-")
        else "repair"
        if run.startswith("repair-")
        else "sync"
        if run.startswith(("sync-", "preview-"))
        else "explore"
        if (rounds and rounds[0].get("warm_start"))
        else "search",
        "owns_test": owns_test,
        "frozen_run": frozen_run,
    }
    repairs = [x for x in evals_all if x.get("kind") == "repair"]
    if status != "running":
        for lane in lanes.values():
            if lane.get("status") == "working":
                lane["status"] = "interrupted"

    tree = []
    for st in read(d / "strategies.jsonl"):
        node = next((n for n in tree if n["id"] == st["id"]), None)
        if node is None:
            node = {"id": st["id"], "parent": st.get("parent"), "round": st.get("round", 0)}
            tree.append(node)
        node["arm"] = (
            st.get("arm") or node.get("arm") or ("reflect" if st.get("parent") else "seed")
        )
        node["playbook"] = st.get("playbook")
        node["flags"] = {
            k: v
            for k, v in (st.get("rule") or {}).items()
            if k in ("retrieval", "samples", "judge")
        }
        if st.get("train"):
            node["train"] = st["train"].get("accuracy")
        if st.get("validation"):
            node["validation"] = st["validation"].get("accuracy")
        if st.get("promotion"):
            node["promoted"] = st["promotion"].get("promoted")
    if rounds and rounds[0].get("warm_start"):
        root = rounds[0]
        if not any(n["id"] == root["incumbent"] for n in tree):
            tree.insert(
                0,
                {
                    "id": root["incumbent"],
                    "parent": None,
                    "round": 0,
                    "arm": "warm start",
                    "train": root["best_train"].get("accuracy"),
                    "validation": root["incumbent_validation"].get("accuracy"),
                },
            )
    incumbents = {r.get("incumbent") for r in rounds}
    for n in tree:
        n["incumbent_ever"] = n["id"] in incumbents
    ladder = None
    lp = ROOT.parent / "results" / f"ladder-{run}-validation.json"
    if lp.exists():
        ladder = json.loads(lp.read_text())

    return {
        "runs": all_runs,
        "run": run,
        "summary": summary_,
        "repairs": repairs,
        "tree": tree,
        "ucb": (rounds[-1].get("ucb") if rounds else None),
        "ladder": ladder,
        "lanes": sorted(lanes.values(), key=lambda x: x["agent"]),
        "rounds": rounds,
        "playbooks": pbs,
        "disagreements": disagreements,
        "alerts": sorted(alerts, key=lambda e: e.get("t", 0))[-40:],
        "decisions": {
            "total": len(decisions),
            "accepted": sum(1 for e in decisions if e.get("accepted")),
            "disagreements": len([e for e in events if e.get("kind") == "disagreement"]),
        },
        "safety": safety,
        "final": final and {"strategy": final["strategy"]["id"], "model": final.get("model")},
        "test": test,
        "events": len(events),
    }


def models() -> dict:
    """Cloud and local models this machine can reach, with the saved default."""
    from forkward import llm, sync

    async def go() -> list[dict]:
        try:
            return await llm.available_models()
        finally:
            await llm.close()

    found = asyncio.run(go())
    for m in found:
        with llm.use_model(m["spec"]):
            try:
                s, *_ = sync.load_strategy()
                m["gate_calibrated"] = sync.gate_threshold_for(s) < sync.UNCALIBRATED
            except (RuntimeError, ValueError):
                m["gate_calibrated"] = False
    return {"default": llm.saved_default(), "models": found}


class Handler(BaseHTTPRequestHandler):
    default_run: str | None = None

    def _send(self, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        q = parse_qs(url.query)
        private_ok = is_admin(self.headers) or is_web_server(self.headers)
        try:
            run = checked_run(q.get("run", [None])[0], private_ok)
        except PermissionError:
            self.send_error(404, "unknown run")
            return
        if url.path == "/api/github/connect":
            # Starting a preview spends model credits: only the web app (for a signed-in user's
            # own fork) or the operator may do it on the public deployment.
            target = requested_target(q, self.headers.get("X-Forkward-Preview"))
            if target is None and locked_down() and not is_admin(self.headers):
                self.send_error(403, "starting a preview needs the operator token")
                return
            body = json.dumps(start_preview(target)).encode()
        elif url.path == "/api/events":
            try:
                since = float(q.get("since", ["0"])[0] or 0)
            except ValueError:
                self.send_error(400, "since must be a number")
                return
            body = json.dumps(event_tail(since, run)).encode()
        elif url.path == "/api/models":
            # Listing models calls the providers; the public deployment does not do that for
            # anonymous visitors.
            if locked_down() and not is_admin(self.headers):
                self.send_error(403, "listing models needs the operator token")
                return
            body = json.dumps(models()).encode()
        elif url.path == "/api/state":
            body = json.dumps(state(run or self.default_run), default=str).encode()
        elif url.path == "/api/audio":
            # The spoken digest of a scheduled sync, behind the same run check as its events.
            mp3 = RUNS / run / "digest.mp3" if run and run != "latest-sync" else None
            if mp3 is None or not mp3.is_file():
                self.send_error(404, "no spoken digest for this run")
                return
            self._send(mp3.read_bytes(), "audio/mpeg")
            return
        elif url.path in ("/", "/index.html"):
            self._send((ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        else:
            self.send_error(404)
            return
        self._send(body)

    def do_POST(self) -> None:
        from forkward import llm

        path = urlparse(self.path).path
        if path == "/api/approval":
            # An approval is a record that a person reviewed the hunks, so it is never anonymous.
            if not is_admin(self.headers):
                self.send_error(403, "approvals need the operator token")
                return
            try:
                length = min(int(self.headers.get("Content-Length", 0)), 4096)
                decision = json.loads(self.rfile.read(length))["decision"]
                if decision not in ("approve", "reject"):
                    raise ValueError("decision must be approve or reject")
            except (ValueError, KeyError, TypeError) as e:
                self.send_error(400, str(e))
                return
            self._send(json.dumps(record_approval(decision)).encode())
            return
        if os.environ.get("BOARD_READONLY"):
            # The public deployment is read-only: visitors cannot change the server's model.
            self.send_error(403, "read-only board")
            return
        if locked_down() and not is_admin(self.headers):
            self.send_error(403, "changing the model needs the operator token")
            return

        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin", "")
        same_origin = origin in (f"http://{host}", "")
        if path != "/api/model" or not same_origin:
            self.send_error(403)
            return
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            self.send_error(415)
            return
        try:
            length = min(int(self.headers.get("Content-Length", 0)), 4096)
            spec = json.loads(self.rfile.read(length))["model"]
            llm.save_default(spec or None)
        except (ValueError, KeyError, TypeError) as e:
            self.send_error(400, str(e))
            return
        self._send(json.dumps({"default": spec}).encode())

    def log_message(self, *args) -> None:
        return


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--run", default=None)
    p.add_argument("--runs", type=Path, default=None, help="runs directory (default: ./runs)")
    a = p.parse_args()
    if a.runs:
        global RUNS
        RUNS = a.runs.resolve()
    Handler.default_run = a.run
    print(f"Forkward board on http://localhost:{a.port}")
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
