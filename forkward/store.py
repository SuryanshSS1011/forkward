"""Run log. Always writes JSONL under runs/<run_id>/, and batches the same docs to Postgres."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from forkward import db, home

COLLECTIONS = ("events", "attempts", "strategies", "playbook_versions", "rounds", "evals")
# Full prompts and responses stay in the local JSONL to keep the database small.
LOCAL_ONLY = {"prompt", "response"}
_SOURCE = Path(__file__).resolve().parents[1]
# A source checkout logs next to the code; an installed package (uvx) logs under the home directory.
RUNS = Path(
    os.environ.get("FORKWARD_RUNS")
    or (_SOURCE / "runs" if (_SOURCE / "pyproject.toml").exists() else home() / "runs")
)


_PG: dict = {}
_PG_LOCK = threading.Lock()
_RETRY_S = 60.0


def _postgres() -> bool:
    """Whether to mirror runs to Postgres. A failed connection is not retried for a minute, so a
    down database does not stall every round."""
    if not db.configured():
        return False
    with _PG_LOCK:
        hit = _PG.get("ok")
        if hit is not None and (hit[0] or time.monotonic() - hit[1] < _RETRY_S):
            return hit[0]
        try:
            with db.system() as cur:
                cur.execute("SELECT 1 FROM run_log LIMIT 1")
            ok = True
        except Exception as e:  # noqa: BLE001 - Postgres is optional here, JSONL is the source of truth
            print(f"[store] Postgres unavailable, JSONL only: {e}", file=sys.stderr)
            ok = False
        _PG["ok"] = (ok, time.monotonic())
        return ok


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, run_id: str | None = None, batch_size: int = 200, observer=None):
        self.run_id = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.dir = RUNS / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.batch_size = batch_size
        self._buf: dict[str, list[dict]] = {c: [] for c in COLLECTIONS}
        self._files = {c: (self.dir / f"{c}.jsonl").open("a") for c in COLLECTIONS}
        self._db = _postgres()
        # Called with every event, for example to mirror a hosted job's progress to Postgres.
        self.observer = observer

    @property
    def mirrored(self) -> bool:
        return self._db

    def add(self, collection: str, doc: dict) -> dict:
        doc = {"run_id": self.run_id, "ts": now(), **doc}
        f = self._files[collection]
        f.write(json.dumps(doc, default=str) + "\n")
        f.flush()
        self._buf[collection].append(doc)
        if len(self._buf[collection]) >= self.batch_size:
            self._flush(collection)
        return doc

    def event(self, agent: str, kind: str, **fields) -> dict:
        doc = self.add("events", {"agent": agent, "kind": kind, "t": time.time(), **fields})
        if self.observer is not None:
            try:
                self.observer(doc)
            except Exception as e:  # noqa: BLE001 - progress reporting must never stop a sync
                print(f"[store] observer failed: {e}", file=sys.stderr)
        return doc

    def _flush(self, collection: str) -> None:
        docs, self._buf[collection] = self._buf[collection], []
        if not self._db or not docs:
            return
        try:
            with db.system() as cur:
                cur.executemany(
                    "INSERT INTO run_log (run_id, collection, ts, doc) VALUES (%s, %s, %s, %s)",
                    [
                        (
                            self.run_id,
                            collection,
                            d["ts"],
                            json.dumps(
                                {k: v for k, v in d.items() if k not in LOCAL_ONLY}, default=str
                            ),
                        )
                        for d in docs
                    ],
                )
        except Exception as e:  # noqa: BLE001 - a failed batch must not crash a round
            print(f"[store] Postgres write to {collection} failed: {e}", file=sys.stderr)

    def flush(self) -> None:
        for c in COLLECTIONS:
            self._flush(c)

    def close(self) -> None:
        self.flush()
        for f in self._files.values():
            f.close()


def read_jsonl(run_dir: Path, collection: str) -> list[dict]:
    p = run_dir / f"{collection}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
