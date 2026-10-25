"""Durable sync jobs, webhooks and MCP auth on a real Postgres (skipped without
TEST_DATABASE_URL)."""

import asyncio
import json
import time

import pytest

from forkward import billing, db, sync, webhooks, workflows


@pytest.fixture
def tenant_repo(pg):
    tid = billing.create_tenant("acme", "team")
    with db.system() as cur:
        cur.execute(
            "INSERT INTO installations (id, tenant_id, account_login, account_type) "
            "VALUES (77, %s, 'acme', 'Organization')",
            (tid,),
        )
        cur.execute(
            "INSERT INTO repositories (id, installation_id, tenant_id, full_name, upstream, "
            "schedule) VALUES (1, 77, %s, 'acme/fork', 'up/r@main', 'daily')",
            (tid,),
        )
    return tid


def test_webhooks_track_repositories_only_for_claimed_installations(tenant_repo):
    added = {
        "action": "added",
        "installation": {"id": 77},
        "repositories_added": [{"id": 2, "full_name": "acme/other"}],
        "repositories_removed": [{"id": 1, "full_name": "acme/fork"}],
    }
    assert webhooks.handle("installation_repositories", added) == {
        "tenant": tenant_repo,
        "added": 1,
        "removed": 1,
    }
    with db.tenant(tenant_repo) as cur:
        cur.execute("SELECT full_name FROM repositories")
        assert [r["full_name"] for r in cur.fetchall()] == ["acme/other"]
        cur.execute("SELECT action FROM audit_events")
        assert [r["action"] for r in cur.fetchall()] == ["installation.repositories"]
    stranger = {**added, "installation": {"id": 999}}
    assert webhooks.handle("installation_repositories", stranger) == {
        "ignored": "unclaimed installation"
    }
    assert webhooks.handle("installation", {"action": "suspend", "installation": {"id": 77}})
    with db.system() as cur:
        cur.execute("SELECT suspended_at FROM installations WHERE id = 77")
        assert cur.fetchone()["suspended_at"] is not None


@pytest.fixture
def worker(pg, monkeypatch):
    """An in-process DBOS worker with short waits, and a fake sync that opens a PR."""
    from dbos import DBOS

    monkeypatch.setattr(workflows, "CI_POLL_S", 1.0)
    monkeypatch.setattr(workflows, "CI_WAIT_S", 30.0)
    monkeypatch.setattr(workflows, "PR_WAIT_S", 30.0)
    with db.system() as cur:
        cur.execute("DROP SCHEMA IF EXISTS dbos CASCADE")
    workflows._DBOS = None
    workflows.start()
    yield
    DBOS.destroy(destroy_registry=False)
    workflows._DBOS = None


def wait_status(tenant, job_id, want, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = workflows.job(tenant, job_id)
        if row and row["status"] in want:
            return row
        time.sleep(0.2)
    raise AssertionError(f"job stuck at {row and row['status']}")


def test_a_sync_job_opens_a_pr_waits_for_ci_and_learns_on_merge(tenant_repo, worker, monkeypatch):
    async def fake_sync(repo, upstream, base=None, open_pr=False, app_push=False, **kw):
        assert open_pr and app_push
        assert kw["branch"] == workflows.job_branch(job_ids[0])
        await kw["on_hunks"](2)
        res = sync.SyncResult(repo, "forkward/sync-1", upstream, False)
        res.hunks = [
            sync.HunkResult("a.c", "a.c#1", "x", 0.95, "auto_apply", "won the vote", "k1"),
            sync.HunkResult("b.c", "b.c#1", "y", 0.40, "needs_review", "disagreed", "k2"),
        ]
        res.pr_url = "https://github.com/acme/fork/pull/5"
        return res

    learned, job_ids = [], []
    monkeypatch.setattr(sync, "sync", fake_sync)
    # Nothing is on GitHub under the job's branch until the fake sync "pushes" forkward/sync-1.
    monkeypatch.setattr(
        workflows,
        "_branch_sha",
        lambda repo, branch, log=True: "c0ffee" if branch == "forkward/sync-1" else None,
    )
    ci = {"status": "pending"}
    monkeypatch.setattr(workflows, "poll_ci", lambda repo, sha: ci["status"])
    monkeypatch.setattr(
        "forkward.history.record_outcome", lambda url: learned.append(url) or {"kept": 1}
    )

    job_id = workflows.create_job(tenant_repo, "acme/fork", "up/r@main")
    job_ids.append(job_id)
    workflows._enqueue(workflows.sync_job, f"sync-{job_id}", job_id)
    row = wait_status(tenant_repo, job_id, {"waiting_ci"})
    assert row["pr_url"].endswith("/pull/5")
    assert [(h["file"], h["decision"], h["applied"]) for h in row["hunks"]] == [
        ("a.c", "auto", True),
        ("b.c", "review", False),
    ]
    # The webhook only wakes the wait; the verdict comes from the Checks API.
    ci["status"] = "success"
    assert workflows.notify_ci("ACME/fork", "c0ffee", "failure") == 1
    wait_status(tenant_repo, job_id, {"waiting_approval"})
    assert workflows.notify_pr("https://github.com/acme/fork/pull/5", merged=True) == 1
    row = wait_status(tenant_repo, job_id, {"succeeded"})
    assert row["summary"]["ci"] == "success" and row["summary"]["pr"] == "merged"
    assert learned == ["https://github.com/acme/fork/pull/5"]
    with db.tenant(tenant_repo) as cur:
        cur.execute("SELECT tool, units, reserved, job_id FROM usage_events")
        rows = cur.fetchall()
    assert [(r["tool"], r["units"], r["reserved"], str(r["job_id"])) for r in rows] == [
        ("open_sync_pr", 2, 2, job_id)
    ]


def test_a_job_for_a_repo_outside_the_tenants_installations_fails_without_pushing(
    tenant_repo, worker, monkeypatch
):
    async def must_not_run(*a, **k):
        raise AssertionError("must not sync")

    monkeypatch.setattr(sync, "sync", must_not_run)
    job_id = workflows.submit(tenant_repo, "someone/else", "up/r@main")
    row = wait_status(tenant_repo, job_id, {"failed"})
    assert "PermissionError" in row["error"]


def test_a_retry_after_the_branch_was_pushed_fails_without_syncing_again(
    tenant_repo, worker, monkeypatch
):
    async def must_not_run(*a, **k):
        raise AssertionError("must not sync")

    monkeypatch.setattr(sync, "sync", must_not_run)
    monkeypatch.setattr(workflows, "_branch_sha", lambda repo, branch, log=True: "c0ffee")
    job_id = workflows.submit(tenant_repo, "acme/fork", "up/r@main")
    row = wait_status(tenant_repo, job_id, {"failed"})
    assert "already on GitHub from an earlier attempt" in row["error"]


def test_units_a_crashed_attempt_reserved_are_returned(tenant_repo):
    job_id = workflows.create_job(tenant_repo, "acme/fork", "up/r@main")
    job = {"id": job_id, "tenant_id": tenant_repo, "repo": "acme/fork"}
    customer = billing.Customer(f"job:{job_id}", "acme", "team", tenant_repo)
    workflows._note_reservation(job, billing.reserve(customer, 5))

    def used():
        with db.tenant(tenant_repo) as cur:
            cur.execute("SELECT used FROM usage_quota")
            return cur.fetchone()["used"]

    assert used() == 5
    workflows.release_abandoned(job, customer)
    assert used() == 0
    with db.tenant(tenant_repo) as cur:
        cur.execute("SELECT units, reserved FROM usage_events")
        assert [(r["units"], r["reserved"]) for r in cur.fetchall()] == [(0, 5)]
        cur.execute("SELECT summary FROM jobs WHERE id = %s", (job_id,))
        assert "reserved" not in cur.fetchone()["summary"]
    workflows.release_abandoned(job, customer)
    assert used() == 0


def test_scheduled_syncs_queue_only_due_repositories(tenant_repo):
    assert workflows.enqueue_due() == 1
    assert workflows.enqueue_due() == 0
    with db.system() as cur:
        cur.execute("SELECT repo, status, workflow_id FROM jobs WHERE repository_id = 1")
        rows = cur.fetchall()
    assert [(r["repo"], r["status"], r["workflow_id"]) for r in rows] == [
        ("acme/fork", "queued", None)
    ]


def test_the_scheduled_workflow_queues_a_job_the_claim_loop_starts(
    tenant_repo, worker, monkeypatch
):
    """The schedule runs inside a DBOS workflow, where starting another workflow from a step
    fails. It used to, leaving the job queued forever."""
    from datetime import datetime

    from dbos import DBOS

    async def fake_sync(repo, upstream, base=None, open_pr=False, app_push=False, **kw):
        await kw["on_hunks"](0)
        return sync.SyncResult(repo, "forkward/sync-1", upstream, True)

    monkeypatch.setattr(sync, "sync", fake_sync)
    DBOS.start_workflow(workflows.scheduled_syncs, datetime.now(), None).get_result()
    with db.system() as cur:
        cur.execute("SELECT id FROM jobs WHERE repository_id = 1")
        (row,) = cur.fetchall()
    assert workflows.claim() == 1
    wait_status(
        tenant_repo, str(row["id"]), {"succeeded", "waiting_ci", "waiting_approval", "failed"}
    )


def test_mcp_accepts_api_keys_and_audience_bound_eddsa_tokens(tenant_repo, monkeypatch):
    import jwt as pyjwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from fastmcp.server.auth.providers.jwt import JWTVerifier

    from forkward import mcp_server

    key = billing.create_key("ci", tenant_id=tenant_repo)
    v = mcp_server.ForkwardVerifier()
    at = asyncio.run(v.verify_token(key))
    assert at.claims["tenant_id"] == tenant_repo and at.claims["plan"] == "team"
    assert asyncio.run(v.verify_token("fw_wrong")) is None
    assert asyncio.run(v.verify_token("eyJ.not.a.jwt")) is None

    sk = Ed25519PrivateKey.generate()
    pub = sk.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    issuer, aud = "https://forkward.example/api/auth", "https://forkward.example/mcp"
    v = mcp_server.ForkwardVerifier(
        JWTVerifier(public_key=pub, issuer=issuer, audience=aud, algorithm="EdDSA")
    )
    with db.system() as cur:
        cur.execute(
            "INSERT INTO tenant_members (tenant_id, user_id) VALUES (%s, 'user-1')",
            (tenant_repo,),
        )

    def token(**over):
        now = int(time.time())
        claims = {"iss": issuer, "aud": aud, "sub": "user-1", "iat": now, "exp": now + 600}
        return pyjwt.encode({**claims, **over}, sk, algorithm="EdDSA")

    at = asyncio.run(v.verify_token(token()))
    assert at is not None and at.claims["tenant_id"] == tenant_repo
    assert asyncio.run(v.verify_token(token(aud="https://evil.example/mcp"))) is None
    assert asyncio.run(v.verify_token(token(exp=int(time.time()) - 10))) is None
    assert asyncio.run(v.verify_token(token(sub="nobody"))) is None


def test_mcp_accepts_auth0_tokens_for_github_users_of_the_web_app(tenant_repo, monkeypatch):
    import jwt as pyjwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastmcp.server.auth.providers.jwt import JWTVerifier

    from forkward import mcp_server

    sk = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = sk.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    issuer, aud = "https://forkward.us.auth0.com/", "https://forkward.example/mcp"
    v = mcp_server.ForkwardVerifier(
        auth0=JWTVerifier(public_key=pub, issuer=issuer, audience=aud, algorithm="RS256")
    )
    with db.system() as cur:
        cur.execute(
            'INSERT INTO "user" (id, name, email, "emailVerified") VALUES'
            " ('user-2', 'Ada', 'ada@example.com', true)"
        )
        cur.execute(
            'INSERT INTO "account" (id, "accountId", "providerId", "userId", "updatedAt")'
            " VALUES ('acc-2', '4242', 'github', 'user-2', now())"
        )
        cur.execute(
            "INSERT INTO tenant_members (tenant_id, user_id) VALUES (%s, 'user-2')",
            (tenant_repo,),
        )

    def token(**over):
        now = int(time.time())
        claims = {"iss": issuer, "aud": aud, "sub": "github|4242", "iat": now, "exp": now + 600}
        return pyjwt.encode({**claims, **over}, sk, algorithm="RS256")

    at = asyncio.run(v.verify_token(token()))
    assert at is not None and at.claims["tenant_id"] == tenant_repo
    # A GitHub account that never signed in to the web app, another connection, a spoofed
    # subject, the wrong audience or issuer, and an expired token are all refused.
    for bad in (
        token(sub="github|9999"),
        token(sub="google-oauth2|4242"),
        token(sub="github|4242' OR '1'='1"),
        token(aud="https://evil.example/mcp"),
        token(iss="https://evil.auth0.com/"),
        token(exp=int(time.time()) - 10),
    ):
        assert asyncio.run(v.verify_token(bad)) is None
    # Without AUTH0_DOMAIN only Better Auth is trusted, so the same token is refused.
    assert asyncio.run(mcp_server.ForkwardVerifier().verify_token(token())) is None


def test_auth_provider_advertises_auth0_when_configured(monkeypatch):
    from forkward import mcp_server

    monkeypatch.setenv("BETTER_AUTH_URL", "https://forkward.example")
    monkeypatch.delenv("FORKWARD_MCP_URL", raising=False)
    monkeypatch.delenv("AUTH0_DOMAIN", raising=False)
    p = mcp_server.auth_provider()
    assert [str(u) for u in p.authorization_servers] == ["https://forkward.example/api/auth"]
    assert p.token_verifier.auth0 is None
    monkeypatch.setenv("AUTH0_DOMAIN", "https://forkward.us.auth0.com/")
    p = mcp_server.auth_provider()
    assert [str(u) for u in p.authorization_servers] == [
        "https://forkward.us.auth0.com/",
        "https://forkward.example/api/auth",
    ]
    a0 = p.token_verifier.auth0
    assert a0.issuer == "https://forkward.us.auth0.com/" and a0.algorithm == "RS256"
    assert a0.jwks_uri == "https://forkward.us.auth0.com/.well-known/jwks.json"
    assert a0.audience == "https://forkward.example/mcp"


def test_webhook_route_rejects_bad_signatures(pg, monkeypatch):
    import hashlib
    import hmac

    from starlette.testclient import TestClient

    from forkward import mcp_server

    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")
    client = TestClient(mcp_server.mcp.http_app())
    body = json.dumps({"zen": "ok"}).encode()
    r = client.post("/github/webhook", content=body, headers={"x-github-event": "ping"})
    assert r.status_code == 401
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    r = client.post(
        "/github/webhook",
        content=body,
        headers={"x-github-event": "ping", "x-hub-signature-256": sig},
    )
    assert r.status_code == 202 and r.json() == {"ok": True}


def test_ci_and_pr_webhooks_wake_waiting_workflows(tenant_repo, monkeypatch):
    woken = []
    monkeypatch.setattr(workflows, "notify_ci", lambda *a: woken.append(("ci", *a)))
    monkeypatch.setattr(workflows, "notify_pr", lambda *a: woken.append(("pr", *a)))
    inst = {"id": 77}
    suite = {
        "action": "completed",
        "installation": inst,
        "repository": {"full_name": "acme/fork"},
        "check_suite": {"head_sha": "c0ffee", "conclusion": "failure"},
    }
    assert webhooks.handle("check_suite", suite)["ci"] == "failure"
    monkeypatch.setenv("GITHUB_APP_ID", "4242")
    own = {**suite, "check_suite": {**suite["check_suite"], "app": {"id": 4242}}}
    assert webhooks.handle("check_suite", own) == {"ignored": "own check suite"}
    pr = {
        "action": "closed",
        "installation": inst,
        "pull_request": {
            "merged": True,
            "html_url": "https://github.com/acme/fork/pull/5",
            "head": {"ref": "forkward/sync-1"},
        },
    }
    assert webhooks.handle("pull_request", pr)["pr"] == "merged"
    other = {**pr, "pull_request": {**pr["pull_request"], "head": {"ref": "feature"}}}
    assert "ignored" in webhooks.handle("pull_request", other)
    assert woken == [
        ("ci", "acme/fork", "c0ffee", "failure"),
        ("pr", "https://github.com/acme/fork/pull/5", True),
    ]


def test_a_sync_that_fails_after_reserving_units_is_not_charged(tenant_repo, worker, monkeypatch):
    async def failing_sync(repo, upstream, base=None, open_pr=False, app_push=False, **kw):
        await kw["on_hunks"](3)
        raise RuntimeError("the merge broke halfway")

    monkeypatch.setattr(sync, "sync", failing_sync)
    job_id = workflows.submit(tenant_repo, "acme/fork", "up/r@main")
    wait_status(tenant_repo, job_id, {"failed"})
    with db.system() as cur:
        cur.execute("SELECT units, reserved FROM usage_events WHERE job_id = %s", (job_id,))
        events = cur.fetchall()
        cur.execute(
            "SELECT coalesce(sum(used), 0) AS used FROM usage_quota WHERE tenant_id = %s",
            (tenant_repo,),
        )
        used = cur.fetchone()["used"]
    assert [(e["units"], e["reserved"] > 0) for e in events] == [(0, True)]
    assert used == 0


def test_a_role_that_bypasses_row_level_security_is_refused():
    class Conn:
        def __init__(self, bypass):
            self.bypass = bypass

        def execute(self, sql):
            assert "rolbypassrls" in sql
            return self

        def fetchone(self):
            return (self.bypass,)

    db.check_role(Conn(False))
    with pytest.raises(db.DatabaseError, match="bypasses row-level security"):
        db.check_role(Conn(True))


def test_reviews_requested_after_the_pr_merged_are_never_pushed(tenant_repo, monkeypatch):
    started = []
    monkeypatch.setattr(workflows, "_enqueue", lambda *a, **k: started.append(a))
    job_id = workflows.create_job(tenant_repo, "acme/fork", "up/r@main")
    with db.tenant(tenant_repo) as cur:
        cur.execute(
            "UPDATE jobs SET status = 'succeeded', summary = '{\"pr\": \"merged\"}', "
            "review_requested = 1 WHERE id = %s",
            (job_id,),
        )
    workflows.claim()
    assert started == []
