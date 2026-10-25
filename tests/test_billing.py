"""Billing enforcement: per-model prices, atomic reservations, usage on failure."""

import asyncio
import threading

import pytest

from forkward import billing, db, mcp_server, sync


@pytest.fixture
def tenant(pg):
    """A trial tenant (500 units) with 10 units already used this month."""
    tid = billing.create_tenant("acme", "trial")
    key = billing.create_key("acme ci", tenant_id=tid)
    c = billing.authenticate(key)
    billing.record(c, "seed", 10)
    return c


def quota_used(c):
    with db.tenant(c.tenant_id) as cur:
        cur.execute("SELECT used FROM usage_quota WHERE tenant_id = %s", (c.tenant_id,))
        row = cur.fetchone()
    return row["used"] if row else None


def usage_rows(c):
    with db.tenant(c.tenant_id) as cur:
        cur.execute(
            "SELECT tool, units, reserved, detail FROM usage_events WHERE tenant_id = %s "
            "ORDER BY ts, id",
            (c.tenant_id,),
        )
        return [{**r.pop("detail"), **r} for r in cur.fetchall()]


def test_unknown_models_are_charged_at_the_highest_rate(monkeypatch):
    monkeypatch.setenv("GENERATOR_MODEL", "deepseek-ai/DeepSeek-V4.1-Flash")
    assert billing.price(None) == 1
    assert billing.price("luminal:deepseek-ai/DeepSeek-V4.1-Flash") == 1
    assert billing.price("anthropic:claude-sonnet-5") == 4
    for spec in ("anthropic:claude-opus-5-20261001", "luminal:some/huge-model", "anthropic:x"):
        assert billing.price(spec) == billing.MAX_UNITS == 8
    assert billing.units_for("anthropic:claude-opus-5-preview", 3) == 24


def test_reservations_are_atomic_and_cap_parallel_calls(tenant):
    c = tenant
    billing.record(c, "seed", 450)  # 500 units, 460 used, 40 left
    ok, refused = [], []

    def go():
        try:
            ok.append(billing.reserve(c, 5))
        except billing.BillingError as e:
            refused.append(str(e))

    threads = [threading.Thread(target=go) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ok) == 8 and len(refused) == 12
    assert quota_used(c) == 500
    assert "needs 5 units" in refused[0]
    # Settling below the reservation gives the rest back and records what was charged.
    billing.settle(ok[0], "resolve_conflicts", 2, status="ok")
    assert quota_used(c) == 497
    rows = usage_rows(c)
    assert rows[-1]["units"] == 2 and rows[-1]["reserved"] == 5
    # A settle can never charge more than was reserved.
    billing.settle(ok[1], "resolve_conflicts", 999, status="ok")
    rows = usage_rows(c)
    assert rows[-1]["units"] == 5 and quota_used(c) == 497


def test_hosted_call_reserves_from_the_hunk_count_and_records_failures(tenant, monkeypatch):
    customer = tenant
    monkeypatch.setenv("FORKWARD_HOSTED", "1")
    monkeypatch.setattr(mcp_server, "HOSTED", True)
    monkeypatch.setattr(mcp_server, "_customer", lambda: customer)
    monkeypatch.setattr(billing, "check_quota", lambda c, needed=1: 40)

    class Ctx:
        async def report_progress(self, **kw):
            return None

    async def too_big(repo, base, upstream, max_hunks=None, on_hunks=None):
        await on_hunks(30)  # 30 hunks x 4 units is far over the 40 left
        raise AssertionError("must not get here")

    monkeypatch.setattr(sync, "preview", too_big)
    with pytest.raises(mcp_server.ToolError, match="needs 120 units"):
        asyncio.run(
            mcp_server.resolve_conflicts(
                "o/fork", "main", "up/r@main", Ctx(), model="anthropic:claude-sonnet-5"
            )
        )
    # The refused reservation rolled back entirely: nothing charged, no counter moved.
    assert [r["tool"] for r in usage_rows(customer)] == ["seed"]
    assert quota_used(customer) in (None, 10)

    async def fails_after_models(repo, base, upstream, max_hunks=None, on_hunks=None):
        await on_hunks(3)
        raise RuntimeError("worker died")

    monkeypatch.setattr(sync, "preview", fails_after_models)
    with pytest.raises(mcp_server.ToolError):
        asyncio.run(mcp_server.resolve_conflicts("o/fork", "main", "up/r@main", Ctx()))
    # The failed call is recorded but not charged, and its reservation returns to the allowance.
    rows = usage_rows(customer)
    assert rows[-1]["units"] == 0 and rows[-1]["status"] == "failed"
    assert quota_used(customer) in (None, 10)

    started = asyncio.Event()

    async def hangs(repo, base, upstream, max_hunks=None, on_hunks=None):
        await on_hunks(2)
        started.set()
        await asyncio.sleep(60)

    monkeypatch.setattr(sync, "preview", hangs)

    async def cancel():
        t = asyncio.create_task(mcp_server.resolve_conflicts("o/fork", "main", "up/r@main", Ctx()))
        await started.wait()
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    asyncio.run(cancel())
    rows = usage_rows(customer)
    assert rows[-1]["units"] == 2 and rows[-1]["status"] == "cancelled"

    async def ok(repo, base, upstream, max_hunks=None, on_hunks=None):
        await on_hunks(3)
        res = sync.SyncResult(repo, "b", upstream, False)
        res.hunks = [
            sync.HunkResult("a.c", "a.c#1", "x", 0.9, "auto_apply", "r"),
            sync.HunkResult("a.c", "a.c#2", "y", 0.4, "needs_review", "r"),
            sync.HunkResult("a.c", "a.c#3", "", 0.0, "blocked", "no candidate"),
        ]
        return res

    monkeypatch.setattr(sync, "preview", ok)
    out = asyncio.run(mcp_server.resolve_conflicts("o/fork", "main", "up/r@main", Ctx()))
    assert out.needs_review == 2
    # Only hunks that got a decision are charged; the blocked one is returned.
    rows = usage_rows(customer)
    assert rows[-1]["units"] == 2 and rows[-1]["reserved"] == 3 and rows[-1]["status"] == "ok"
    assert quota_used(customer) == 10 + 0 + 2 + 2  # seed, failed (free), cancelled, ok


def test_hosted_service_refuses_local_models(monkeypatch):
    with pytest.raises(mcp_server.ToolError):
        mcp_server._check_model("local:qwen", hosted=True)
    mcp_server._check_model("local:qwen")


def test_row_level_security_hides_other_tenants(tenant):
    other = billing.authenticate(billing.create_key("globex", "team"))
    billing.record(other, "resolve_conflicts", 7, repo="globex/fork")
    with db.tenant(tenant.tenant_id) as cur:
        cur.execute("SELECT tenant_id, units FROM usage_events")
        assert {r["tenant_id"] for r in cur.fetchall()} == {tenant.tenant_id}
        cur.execute("SELECT count(*) AS n FROM api_keys")
        assert cur.fetchone()["n"] == 1
    # Writing another tenant's row from this tenant's scope is refused by the policy.
    with (
        pytest.raises(Exception, match="row-level security"),
        db.tenant(tenant.tenant_id) as cur,
    ):
        cur.execute(
            "INSERT INTO usage_events (tenant_id, tool, units) VALUES (%s, 'x', 1)",
            (other.tenant_id,),
        )
    # A connection that never set a scope sees nothing.
    with db.pool().connection() as conn:
        assert conn.execute("SELECT count(*) FROM usage_events").fetchone()[0] == 0


def test_audit_events_are_append_only(tenant):
    with db.tenant(tenant.tenant_id) as cur:
        cur.execute(
            "INSERT INTO audit_events (tenant_id, actor, action) VALUES (%s, 'system', 'x') "
            "RETURNING id",
            (tenant.tenant_id,),
        )
        rid = cur.fetchone()["id"]
    with pytest.raises(Exception, match="append-only"), db.tenant(tenant.tenant_id) as cur:
        cur.execute("UPDATE audit_events SET action = 'y' WHERE id = %s", (rid,))


def test_revoked_keys_stop_working_and_usage_reports_once_to_stripe(tenant):
    with db.system() as cur:
        cur.execute(
            "UPDATE tenants SET stripe_customer_id = 'cus_TEST1' WHERE id = %s",
            (tenant.tenant_id,),
        )
    billing.record(tenant, "resolve_conflicts", 4, repo="acme/fork")
    sent = []

    class Resp:
        status_code = 200
        text = "{}"

    def post(data):
        sent.append(data)
        return Resp()

    assert billing.report_to_stripe(post=post) == {"sent": 2, "failed": 0}
    assert sorted(int(d["payload[value]"]) for d in sent) == [4, 10]
    assert all(d["payload[stripe_customer_id]"] == "cus_TEST1" for d in sent)
    assert len({d["identifier"] for d in sent}) == 2
    assert billing.report_to_stripe(post=post) == {"sent": 0, "failed": 0}
    assert billing.revoke(tenant.key_id)
    with pytest.raises(billing.BillingError, match="revoked"):
        billing.authenticate("fw_not_a_real_key")
