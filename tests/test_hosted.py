"""Hosted MCP server: no blocked event loop, per-key limits, shared clients, and no pushes
except through the GitHub App."""

import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest

from forkward import billing, llm, mcp_server, sandbox, sync


class Ctx:
    async def report_progress(self, **kw):
        return None


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv("FORKWARD_HOSTED", "1")
    monkeypatch.setattr(mcp_server, "HOSTED", True)
    customer = billing.Customer("fw_abc...wxyz", "acme", "team")
    monkeypatch.setattr(mcp_server, "_customer", lambda: customer)
    monkeypatch.setattr(billing, "check_quota", lambda c, needed=1: 100)
    recorded = []
    monkeypatch.setattr(billing, "record", lambda c, tool, units, **d: recorded.append(units))
    return recorded


def test_one_inflight_sync_per_api_key(hosted, monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_preview(repo, base, upstream, max_hunks=None, on_hunks=None):
        assert max_hunks == mcp_server.MAX_HUNKS
        started.set()
        await release.wait()
        return sync.SyncResult(repo, "forkward/preview-x", upstream, True)

    monkeypatch.setattr(sync, "preview", fake_preview)

    async def go():
        first = asyncio.create_task(
            mcp_server.resolve_conflicts("o/fork", "main", "up/repo@main", Ctx())
        )
        await started.wait()
        with pytest.raises(mcp_server.ToolError, match="already running"):
            await mcp_server.resolve_conflicts("o/fork", "main", "up/repo@main", Ctx())
        release.set()
        await first
        # The slot is released afterwards, also when the call fails.
        release.clear()
        started.clear()
        second = asyncio.create_task(
            mcp_server.resolve_conflicts("o/fork", "main", "up/repo@main", Ctx())
        )
        await started.wait()
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        assert not mcp_server._INFLIGHT

    asyncio.run(go())


def test_hosted_inputs_are_validated(hosted):
    for repo, base, branch in (
        ("../forkward-data", "HEAD", "a/b@main"),
        ("o/fork", "-x", "a/b@main"),
        ("o/fork", "main", "a/b@+refs/heads/main:refs/tags/v1"),
        ("/srv/repo", "main", "a/b@main"),
    ):
        with pytest.raises(mcp_server.ToolError):
            asyncio.run(mcp_server.resolve_conflicts(repo, base, branch, Ctx()))


def test_hosted_never_pushes_without_the_github_app(hosted, tmp_path, monkeypatch):
    for k in ("GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY", "GITHUB_APP_PRIVATE_KEY_PATH"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(mcp_server.ToolError, match="no GitHub App"):
        asyncio.run(mcp_server.open_sync_pr("o/fork", "up/repo@main", Ctx()))
    with pytest.raises(sync.PushRefused):
        asyncio.run(sync.sync("o/fork", "up/repo@main", open_pr=True))
    with pytest.raises(sync.PushRefused):
        sync.safe_push(tmp_path, "forkward/sync-1")
    res = sync.SyncResult("o/fork", "forkward/sync-1", "up/repo@main", True)
    with pytest.raises(sync.PushRefused):
        sync.open_pull_request(tmp_path, "o/fork", "forkward/sync-1", "main", res)
    with pytest.raises(mcp_server.ToolError, match="local install"):
        mcp_server.record_outcome("https://github.com/o/fork/pull/1")
    assert sync.run_id("preview").startswith("private-preview-")
    assert len(sync.run_id("preview").rsplit("-", 1)[1]) == 32
    assert sync.run_id("preview", private=False).startswith("preview-")


def test_git_work_runs_off_the_event_loop(monkeypatch, tmp_path):
    monkeypatch.delenv("FORKWARD_HOSTED", raising=False)
    monkeypatch.delenv("FORKWARD_TRANSPORT", raising=False)
    monkeypatch.setattr(llm, "load_env", lambda path=None: None)
    monkeypatch.setattr(sync, "RUNS", tmp_path)
    monkeypatch.setattr("forkward.store.RUNS", tmp_path)
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.setattr(sync, "lock_target", lambda repo: tmp_path / "lock")
    monkeypatch.setattr(sync, "CACHE", tmp_path / "cache")

    def slow_prepare(*a):
        time.sleep(0.6)
        raise RuntimeError("clone failed")

    monkeypatch.setattr(sync, "prepare", slow_prepare)

    async def go():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.05)
                ticks += 1

        t = asyncio.create_task(ticker())
        with pytest.raises(RuntimeError, match="clone failed"):
            await sync.preview("o/fork", "main", "up/repo@main")
        t.cancel()
        return ticks

    assert asyncio.run(go()) >= 5


def test_too_many_hunks_are_refused_before_any_model_call(monkeypatch, tmp_path):
    monkeypatch.setattr(sync, "conflict_hunks", lambda path, sha, skipped=None: [object()] * 5)

    def no_strategy():
        raise AssertionError("no model work for an oversized job")

    monkeypatch.setattr(sync, "load_strategy", no_strategy)
    with pytest.raises(sync.TooLarge):
        asyncio.run(sync.resolve(tmp_path, "abc", None, max_hunks=4))
    seen = []

    async def on_hunks(n):
        seen.append(n)
        raise billing.BillingError("quota")

    with pytest.raises(billing.BillingError):
        asyncio.run(sync.resolve(tmp_path, "abc", None, max_hunks=10, on_hunks=on_hunks))
    assert seen == [5]


def test_repo_size_limit(monkeypatch, tmp_path):
    out = "count: 0\nsize: 100\nin-pack: 10\npacks: 1\nsize-pack: 3000000\n"
    monkeypatch.setattr(sync, "git", lambda *a, **k: out)
    monkeypatch.setattr(sync, "MAX_REPO_MB", 2048)
    with pytest.raises(sync.TooLarge):
        sync.check_size(tmp_path)
    monkeypatch.setattr(sync, "MAX_REPO_MB", 4096)
    sync.check_size(tmp_path)


def test_close_never_tears_down_clients_a_server_is_still_using(monkeypatch):
    monkeypatch.setattr(llm, "_CLIENT", None)
    monkeypatch.setattr(llm, "PERSISTENT", True)

    async def go():
        c = llm._client()
        s = llm._sem()
        await llm.close()
        assert llm._client() is c and not c.is_closed and llm._sem() is s
        return c

    c1 = asyncio.run(go())
    # Each event loop (for example a board thread) gets its own client.
    c2 = asyncio.run(go())
    assert c1 is not c2
    monkeypatch.setattr(llm, "PERSISTENT", False)

    async def cli():
        c = llm._client()
        await llm.close()
        return c

    assert asyncio.run(cli()).is_closed


def test_a_closed_client_is_a_failed_call_not_a_crash(monkeypatch):
    def handler(request):
        raise RuntimeError("Cannot send a request, as the client has been closed.")

    monkeypatch.setattr(llm, "load_env", lambda path=None: None)
    monkeypatch.setenv("LLM_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_MODEL", "m")
    monkeypatch.setattr(llm, "_CLIENT", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    c = asyncio.run(llm.complete([{"role": "user", "content": "hi"}], retries=2))
    assert not c.ok and "closed" in c.error


async def _no_sleep(*a, **k):
    return None


def test_retry_after_is_parsed_defensively():
    assert llm.retry_after(None) == 1.0
    assert llm.retry_after("2.5") == 2.5
    assert llm.retry_after("86400") == llm.RETRY_AFTER_MAX
    assert llm.retry_after("-5") == 0.0
    assert llm.retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0
    assert llm.retry_after("soon") == 1.0
    assert llm.retry_after("nan") == 1.0


def test_http_requests_always_need_a_key(monkeypatch):
    monkeypatch.setattr(mcp_server, "HOSTED", False)
    monkeypatch.setattr(mcp_server, "get_http_request", lambda: SimpleNamespace(query_params={}))
    monkeypatch.setattr(mcp_server, "get_http_headers", lambda include=None: {})
    with pytest.raises(mcp_server.ToolError, match="missing API key"):
        mcp_server._customer()


def test_get_updates_bounds_its_inputs(monkeypatch):
    monkeypatch.setattr(mcp_server, "_customer", lambda: None)
    seen = {}
    monkeypatch.setattr(sync, "updates", lambda since, limit: seen.update(limit=limit) or [])
    mcp_server.get_updates(None, 10**9)
    assert seen["limit"] == 100
    with pytest.raises(mcp_server.ToolError):
        mcp_server.get_updates(1e300)


def test_scheduled_syncs_on_the_hosted_box_only_preview(monkeypatch, tmp_path):
    from forkward import history, schedule

    monkeypatch.setenv("FORKWARD_HOSTED", "1")
    monkeypatch.setattr("forkward.store.RUNS", tmp_path)
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.setattr(history, "learn_pending", list)
    monkeypatch.setattr(
        schedule,
        "entries",
        lambda: [
            {"repo": "o/fork", "upstream": "up/r@main", "open_pr": True, "build": "cargo build"}
        ],
    )
    monkeypatch.setattr(mcp_server, "_github_ready", lambda: True)
    calls = []

    async def fake_preview(repo, base, upstream, run=None):
        assert not run.startswith("private-")
        calls.append("preview")
        return sync.SyncResult(repo, "b", upstream, True)

    async def fake_sync(*a, **k):
        raise AssertionError("the hosted box must not push")

    monkeypatch.setattr(sync, "preview", fake_preview)
    monkeypatch.setattr(sync, "sync", fake_sync)
    out = asyncio.run(schedule.run_all())
    assert calls == ["preview"] and out[0]["ok"]
    assert sandbox.hosted()


def test_api_keys_are_redacted_from_logs():
    import logging

    f = mcp_server.RedactKeys()
    rec = logging.LogRecord(
        "uvicorn.error",
        logging.INFO,
        __file__,
        1,
        '%s "GET /mcp?key=%s&x=1"',
        ("1.2.3.4", "fw_secretKEY123"),
        None,
    )
    assert f.filter(rec)
    msg = rec.getMessage()
    assert "fw_secretKEY123" not in msg and "key=[redacted]&x=1" in msg
    rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "bad key fw_abcdefghijkl", None, None)
    f.filter(rec)
    assert "fw_abcdefghijkl" not in rec.getMessage()


def test_remote_size_is_checked_before_any_clone(monkeypatch, tmp_path):
    sizes = {"o/fork": 10, "torvalds/linux": 5_000_000}

    def fake_get(url, headers=None, timeout=None):
        repo = url.split("/repos/", 1)[1]
        if repo not in sizes:
            return httpx.Response(404, request=httpx.Request("GET", url))
        return httpx.Response(200, json={"size": sizes[repo]}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(sync, "MAX_REPO_MB", 2048)
    sync.check_remote_size("o/fork")
    with pytest.raises(sync.TooLarge, match="torvalds/linux"):
        sync.check_remote_size("torvalds/linux")
    with pytest.raises(RuntimeError, match="could not look up"):
        sync.check_remote_size("o/missing")

    monkeypatch.setenv("FORKWARD_HOSTED", "1")
    monkeypatch.setattr(sync, "CACHE", tmp_path / "cache")

    def no_clone(*a, **k):
        raise AssertionError("must not clone")

    monkeypatch.setattr(sync, "checkout", no_clone)
    with pytest.raises(sync.TooLarge):
        sync.prepare("o/fork", "main", "torvalds/linux@master", "preview", tmp_path / "lock")


def test_cache_prunes_least_recently_used_clones_it_can_safely_remove(monkeypatch, tmp_path):
    import os

    cache = tmp_path / "cache"
    monkeypatch.setattr(sync, "CACHE", cache)
    monkeypatch.setattr(sync, "CACHE_MAX_MB", 3.5)
    clones = {}
    for age, name in enumerate(["busy", "oldest", "older", "current", "newest"]):
        d = cache / name
        (d / ".git").mkdir(parents=True)
        (d / ".git" / "pack").write_bytes(b"x" * 1024 * 1024)
        os.utime(d, (1000 + age, 1000 + age))
        clones[name] = d
    (clones["busy"] / ".git" / "worktrees" / "preview").mkdir(parents=True)
    (cache / "locks").mkdir()
    assert sync.prune_cache(keep=clones["current"]) == ["oldest", "older"]
    assert sorted(p.name for p in cache.iterdir()) == ["busy", "current", "locks", "newest"]
