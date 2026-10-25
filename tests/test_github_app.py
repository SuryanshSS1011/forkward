"""The forkward-bot GitHub App: JWTs, scoped tokens, check runs, CI status and webhooks."""

import hashlib
import hmac
import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from forkward import github_app, sync


@pytest.fixture
def app(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", pem.replace("\n", "\\n"))
    monkeypatch.setenv("GITHUB_APP_SLUG", "forkward-bot")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    github_app._TOKENS.clear()
    return key.public_key()


def mock_github(monkeypatch, handler):
    calls = []

    def wrapped(request):
        calls.append(request)
        return handler(request)

    monkeypatch.setattr(
        github_app,
        "_client",
        lambda: httpx.Client(base_url=github_app.API, transport=httpx.MockTransport(wrapped)),
    )
    return calls


def test_app_jwt_is_short_lived_and_signed_with_the_app_key(app):
    token = github_app.app_jwt(now=1_000_000)
    claims = jwt.decode(token, app, algorithms=["RS256"], options={"verify_exp": False})
    assert claims == {"iat": 999_940, "exp": 1_000_540, "iss": "12345"}


def test_installation_tokens_are_scoped_to_one_repo_and_cached(app, monkeypatch):
    def handler(req):
        if req.url.path == "/repos/acme/fork/installation":
            return httpx.Response(200, json={"id": 77})
        assert req.url.path == "/app/installations/77/access_tokens"
        assert req.headers["authorization"].startswith("Bearer ey")
        body = json.loads(req.content)
        assert body["repositories"] == ["fork"]
        assert body["permissions"] == github_app.SYNC_PERMISSIONS
        exp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
        return httpx.Response(201, json={"token": "ghs_scoped", "expires_at": exp})

    calls = mock_github(monkeypatch, handler)
    assert github_app.token_for("acme/fork") == "ghs_scoped"
    assert github_app.token_for("acme/fork") == "ghs_scoped"
    assert [c.url.path for c in calls].count("/app/installations/77/access_tokens") == 1


def test_without_the_app_the_bot_token_or_local_login_is_used(monkeypatch):
    for k in ("GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY", "GITHUB_APP_PRIVATE_KEY_PATH"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_bot")
    assert github_app.token_for("acme/fork") == "ghp_bot"
    monkeypatch.delenv("GITHUB_TOKEN")
    assert github_app.token_for("acme/fork") is None
    assert sync.repo_token("/a/local/path") is None


def test_check_run_requires_action_while_any_hunk_needs_review():
    hunks = [
        sync.HunkResult("a.c", "a.c#1", "x", 0.95, "auto_apply", "won the vote"),
        sync.HunkResult("b.c", "b.c#1", "y", 0.4, "needs_review", "classifier disagreed"),
        sync.HunkResult("c.c", "c.c#1", "", 0.0, "blocked", "no candidate"),
    ]
    out = github_app.check_run_payload("abc", hunks, "summary")
    assert out["conclusion"] == "action_required"
    assert out["output"]["title"] == "2 of 3 hunks need review"
    levels = {a["path"]: a["annotation_level"] for a in out["output"]["annotations"]}
    assert levels == {"b.c": "warning", "c.c": "failure"}
    assert github_app.check_run_payload("abc", hunks[:1], "s")["conclusion"] == "success"


def test_review_check_succeeds_only_when_no_hunk_is_left():
    out = github_app.review_check_payload("def", 3, 2, 1)
    assert out["head_sha"] == "def" and out["name"] == github_app.CHECK_NAME
    assert out["conclusion"] == "action_required"
    assert out["output"]["title"] == "1 of 3 hunks still need review"
    done = github_app.review_check_payload("def", 3, 3, 0)
    assert done["conclusion"] == "success" and done["output"]["title"] == "All 3 hunks resolved"


def test_ci_status_ignores_forkwards_own_check(monkeypatch):
    runs = []
    monkeypatch.setattr(github_app, "request", lambda *a, **k: {"check_runs": runs})
    assert github_app.ci_status("t", "acme/fork", "abc") == "none"
    runs.append({"name": github_app.CHECK_NAME, "status": "completed", "conclusion": "failure"})
    assert github_app.ci_status("t", "acme/fork", "abc") == "none"
    runs.append({"name": "build", "status": "in_progress", "conclusion": None})
    assert github_app.ci_status("t", "acme/fork", "abc") == "pending"
    runs[-1] = {"name": "build", "status": "completed", "conclusion": "success"}
    assert github_app.ci_status("t", "acme/fork", "abc") == "success"
    runs.append({"name": "test", "status": "completed", "conclusion": "timed_out"})
    assert github_app.ci_status("t", "acme/fork", "abc") == "failure"


def test_webhook_signatures_are_checked_in_constant_time():
    body = b'{"zen": "x"}'
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert github_app.verify_webhook(body, sig, "s3cret")
    assert not github_app.verify_webhook(body + b" ", sig, "s3cret")
    assert not github_app.verify_webhook(body, sig, "")
    assert not github_app.verify_webhook(body, None, "s3cret")
    assert not github_app.verify_webhook(body, sig.replace("sha256=", "sha1="), "s3cret")


def test_app_pull_requests_go_through_rest_with_the_installation_token(app, monkeypatch, tmp_path):
    seen = {}

    def create_pull(token, repo, head, base, title, body, draft):
        seen.update(token=token, repo=repo, head=head, base=base, draft=draft)
        return "https://github.com/acme/fork/pull/9"

    monkeypatch.setattr(github_app, "create_pull", create_pull)
    monkeypatch.setattr(github_app, "post_check_run", lambda *a: seen.update(check=a[2]) or "u")
    monkeypatch.setattr(sync, "git", lambda path, *a, **k: "deadbeef\n")
    monkeypatch.setattr(sync, "pr_body", lambda r: "body")
    res = sync.SyncResult("acme/fork", "forkward/sync-1", "up/r@main", False)
    res.hunks = [sync.HunkResult("b.c", "b.c#1", "y", 0.4, "needs_review", "r")]
    url = sync.open_pull_request(tmp_path, "acme/fork", "forkward/sync-1", "main", res, "ghs_t")
    assert url.endswith("/pull/9")
    assert seen == {
        "token": "ghs_t",
        "repo": "acme/fork",
        "head": "forkward/sync-1",
        "base": "main",
        "draft": True,
        "check": "deadbeef",
    }


def test_hosted_pushes_need_the_app_and_an_installation_token(app, monkeypatch, tmp_path):
    monkeypatch.setenv("FORKWARD_HOSTED", "1")
    with pytest.raises(sync.PushRefused):
        sync.safe_push(tmp_path, "forkward/sync-1")
    with pytest.raises(sync.PushRefused):
        sync.safe_push(tmp_path, "forkward/sync-1", "ghs_t", app_push=False)
    with pytest.raises(sync.PushRefused, match="only forkward"):
        sync.safe_push(tmp_path, "main", "ghs_t", app_push=True)


def test_commits_are_authored_by_the_app_bot(app, monkeypatch):
    monkeypatch.delenv("FORKWARD_GIT_NAME", raising=False)
    monkeypatch.delenv("FORKWARD_GIT_EMAIL", raising=False)
    github_app._IDENTITY.clear()
    mock_github(monkeypatch, lambda req: httpx.Response(200, json={"id": 4242}))
    assert sync.commit_identity() == [
        "-c",
        "user.name=forkward-bot[bot]",
        "-c",
        "user.email=4242+forkward-bot[bot]@users.noreply.github.com",
    ]
