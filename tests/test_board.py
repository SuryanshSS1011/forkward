"""The public board: operator token for writes and spend, and no customer runs in public."""

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from dashboard import server


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "RUNS", tmp_path)
    monkeypatch.setenv("BOARD_ADMIN_TOKEN", "op-token")
    monkeypatch.setattr(server, "preview_secret", lambda: "web-secret")
    monkeypatch.delenv("BOARD_READONLY", raising=False)
    for name in ("preview-20261012T000000-abcdef", "private-preview-20261012T000001-" + "a" * 32):
        (tmp_path / name).mkdir()
        (tmp_path / name / "events.jsonl").write_text(
            json.dumps({"agent": "sync", "kind": "alert", "t": 1, "message": f"run {name}"}) + "\n"
        )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    def call(method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=10)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, r.read()

    yield call
    httpd.shutdown()


PRIVATE = "private-preview-20261012T000001-" + "a" * 32


def test_private_runs_are_never_listed_or_readable_without_a_secret(board):
    status, body = board("GET", "/api/state")
    state = json.loads(body)
    assert status == 200 and state["runs"] == ["preview-20261012T000000-abcdef"]
    assert PRIVATE.encode() not in body
    assert board("GET", f"/api/state?run={PRIVATE}")[0] == 404
    assert board("GET", f"/api/events?run={PRIVATE}")[0] == 404
    status, body = board(
        "GET", f"/api/events?run={PRIVATE}", headers={"X-Forkward-Preview": "web-secret"}
    )
    assert status == 200 and PRIVATE.encode() in body
    status, _ = board(
        "GET", f"/api/state?run={PRIVATE}", headers={"Authorization": "Bearer op-token"}
    )
    assert status == 200
    for bad in ("../../etc", "%2Fetc", "nope", "/tmp"):
        assert board("GET", f"/api/events?run={bad}")[0] == 404


def test_spoken_digests_follow_the_run_privacy_check(board, tmp_path):
    public = "preview-20261012T000000-abcdef"
    assert board("GET", f"/api/audio?run={public}")[0] == 404
    (tmp_path / public / "digest.mp3").write_bytes(b"ID3fake")
    (tmp_path / PRIVATE / "digest.mp3").write_bytes(b"ID3secret")
    assert board("GET", f"/api/audio?run={public}") == (200, b"ID3fake")
    assert board("GET", f"/api/audio?run={PRIVATE}")[0] == 404
    assert board("GET", "/api/audio")[0] == 404
    assert board("GET", "/api/audio?run=../../etc")[0] == 404


def test_writes_and_spend_need_the_operator_token(board, monkeypatch):
    approve = json.dumps({"decision": "approve"})
    assert board("POST", "/api/approval", approve, {"Content-Type": "application/json"})[0] == 403
    assert board("POST", "/api/approval", approve, {"Authorization": "Bearer wrong"})[0] == 403
    status, body = board(
        "POST",
        "/api/approval",
        approve,
        {"Authorization": "Bearer op-token", "Content-Length": str(len(approve))},
    )
    assert status == 200 and json.loads(body)["ok"]
    assert board("GET", "/api/models")[0] == 403
    assert board("GET", "/api/github/connect")[0] == 403
    assert (
        board(
            "POST", "/api/model", json.dumps({"model": None}), {"Content-Type": "application/json"}
        )[0]
        == 403
    )
    started = []
    monkeypatch.setattr(server, "start_preview", lambda target=None: started.append(target) or {})
    assert (
        board("GET", "/api/github/connect", headers={"Authorization": "Bearer op-token"})[0] == 200
    )
    assert started == [None]


def test_a_visitors_fork_preview_is_a_private_run(monkeypatch, tmp_path):
    from forkward import sync

    monkeypatch.setattr(server, "JOB", {"running": False, "started": 0.0})
    calls = []

    class FakeThread:
        def __init__(self, target, daemon):
            calls.append(target)

        def start(self):
            return None

    import threading as _threading

    monkeypatch.setattr(_threading, "Thread", FakeThread)
    out = server.start_preview({"repo": "alice/fork", "upstream": "up/r@main", "base": "HEAD"})
    assert out["run"].startswith(sync.PRIVATE_PREFIX) and out["started"]
    monkeypatch.setattr(server, "JOB", {"running": False, "started": 0.0})
    monkeypatch.setattr(server, "demo_target", lambda: {"repo": "op/demo", "upstream": "up/r@main"})
    out = server.start_preview(None)
    assert out["run"].startswith("preview-")
