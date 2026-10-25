"""The forkward-bot GitHub App: app JWTs, short-lived installation tokens, REST calls, check runs
and webhook verification.

Configuration comes from the environment:

    GITHUB_APP_ID            numeric app id
    GITHUB_APP_SLUG          e.g. forkward-bot
    GITHUB_APP_PRIVATE_KEY   the PEM text, or GITHUB_APP_PRIVATE_KEY_PATH pointing at it
    GITHUB_WEBHOOK_SECRET    shared secret for webhook signatures

When the app is not configured, `token_for` falls back to GITHUB_TOKEN, and with neither set the
local install keeps using the user's own git credentials and `gh` login. Installation tokens are
scoped to the single repository being synced and expire within an hour, so a leaked token cannot
reach the tenant's other repositories.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

# GITHUB_API_URL points at GitHub Enterprise Server, or at a fake API in end-to-end tests.
API = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
API_VERSION = "2022-11-28"
CHECK_NAME = "Forkward gate"
# The permissions a sync needs, and nothing else. Requested explicitly on every token.
SYNC_PERMISSIONS = {
    "contents": "write",
    "pull_requests": "write",
    "checks": "write",
    "metadata": "read",
}


class GitHubAppError(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.environ.get("GITHUB_APP_ID") and _private_key_text())


def _private_key_text() -> str | None:
    if os.environ.get("GITHUB_APP_PRIVATE_KEY"):
        return os.environ["GITHUB_APP_PRIVATE_KEY"].replace("\\n", "\n")
    p = os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH")
    return Path(p).read_text() if p and Path(p).is_file() else None


def app_jwt(now: float | None = None) -> str:
    """A 9-minute RS256 JWT that authenticates as the app itself."""
    import jwt

    key = _private_key_text()
    app_id = os.environ.get("GITHUB_APP_ID")
    if not key or not app_id:
        raise GitHubAppError("GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY are not set")
    t = int(now if now is not None else time.time())
    # Backdated a minute for clock drift; GitHub rejects tokens that live past ten minutes.
    return jwt.encode({"iat": t - 60, "exp": t + 540, "iss": app_id}, key, algorithm="RS256")


def _headers(token: str | None = None, bearer: bool = True) -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION}
    if token:
        h["Authorization"] = f"Bearer {token}" if bearer else f"token {token}"
    return h


def _client() -> httpx.Client:
    return httpx.Client(base_url=API, timeout=30)


def _check(r: httpx.Response, what: str) -> dict | list:
    if r.status_code >= 300:
        # GitHub error bodies carry a message and a docs URL, never secrets.
        try:
            msg = r.json().get("message", "")
        except ValueError:
            msg = r.text[:200]
        raise GitHubAppError(f"{what} failed: HTTP {r.status_code} {msg}")
    return r.json() if r.content else {}


def installation_id(repo: str) -> int:
    """The installation that covers owner/name, from the database when it is known there and
    from the GitHub API otherwise."""
    from forkward import db

    if db.configured():
        try:
            with db.system() as cur:
                cur.execute(
                    "SELECT installation_id FROM repositories WHERE lower(full_name) = lower(%s) "
                    "AND enabled",
                    (repo,),
                )
                row = cur.fetchone()
            if row:
                return int(row["installation_id"])
        except Exception as e:  # noqa: BLE001 - fall back to asking GitHub
            print(f"[github_app] installation lookup in the database failed: {e}", file=sys.stderr)
    with _client() as c:
        data = _check(
            c.get(f"/repos/{repo}/installation", headers=_headers(app_jwt())),
            f"looking up the installation for {repo}",
        )
    return int(data["id"])


@dataclass
class _Cached:
    token: str
    expires: float


_TOKENS: dict[tuple, _Cached] = {}
_LOCK = threading.Lock()


def installation_token(
    installation: int, repo: str | None = None, permissions: dict[str, str] | None = None
) -> str:
    """A token for one installation, narrowed to `repo` (owner/name) and `permissions`. Cached
    until five minutes before it expires."""
    perms = permissions or SYNC_PERMISSIONS
    key = (installation, repo, tuple(sorted(perms.items())))
    with _LOCK:
        hit = _TOKENS.get(key)
        if hit and hit.expires - time.time() > 300:
            return hit.token
    body: dict = {"permissions": perms}
    if repo:
        body["repositories"] = [repo.split("/", 1)[1]]
    with _client() as c:
        data = _check(
            c.post(
                f"/app/installations/{installation}/access_tokens",
                headers=_headers(app_jwt()),
                json=body,
            ),
            "minting an installation token",
        )
    from datetime import datetime

    expires = datetime.fromisoformat(data["expires_at"]).timestamp()
    with _LOCK:
        _TOKENS[key] = _Cached(data["token"], expires)
    return data["token"]


def token_for(repo: str) -> str | None:
    """The credential a sync of owner/name pushes and opens pull requests with: an installation
    token when the app is configured, else GITHUB_TOKEN, else None (the local git login)."""
    if configured():
        return installation_token(installation_id(repo), repo)
    return os.environ.get("GITHUB_TOKEN") or None


_IDENTITY: dict[str, tuple[str, str]] = {}


def bot_identity() -> tuple[str, str] | None:
    """The git author name and noreply email GitHub attributes to the app's bot user."""
    slug = os.environ.get("GITHUB_APP_SLUG")
    if not slug:
        return None
    if slug not in _IDENTITY:
        login = f"{slug}[bot]"
        with _client() as c:
            data = _check(c.get(f"/users/{login}", headers=_headers()), "looking up the bot user")
        _IDENTITY[slug] = (login, f"{data['id']}+{login}@users.noreply.github.com")
    return _IDENTITY[slug]


def request(token: str, method: str, path: str, **kw) -> dict | list:
    with _client() as c:
        return _check(c.request(method, path, headers=_headers(token), **kw), f"{method} {path}")


def create_pull(
    token: str, repo: str, head: str, base: str, title: str, body: str, draft: bool
) -> str:
    data = request(
        token,
        "POST",
        f"/repos/{repo}/pulls",
        json={"head": head, "base": base, "title": title, "body": body, "draft": draft},
    )
    return data["html_url"]


def check_run_payload(sha: str, hunks: list, summary: str) -> dict:
    """One check run per sync on the merge commit. It concludes `action_required` while any hunk
    needs a person and `success` when every hunk passed the gate; each review hunk becomes an
    annotation on its file (GitHub accepts at most 50 per request)."""
    review = [h for h in hunks if h.gate != "auto_apply"]
    annotations = [
        {
            "path": h.file,
            "start_line": 1,
            "end_line": 1,
            "annotation_level": "warning" if h.gate == "needs_review" else "failure",
            "title": f"{h.hunk_id}: {h.gate.replace('_', ' ')}",
            "message": (h.reason or "the gate did not certify this hunk")[:1000],
        }
        for h in review[:50]
    ]
    return {
        "name": CHECK_NAME,
        "head_sha": sha,
        "status": "completed",
        "conclusion": "action_required" if review else "success",
        "output": {
            "title": (
                f"{len(review)} of {len(hunks)} hunks need review"
                if review
                else f"All {len(hunks)} hunks passed the gate"
            ),
            "summary": summary[:65000],
            "annotations": annotations,
        },
    }


def post_check_run(token: str, repo: str, sha: str, hunks: list, summary: str) -> str:
    data = request(
        token, "POST", f"/repos/{repo}/check-runs", json=check_run_payload(sha, hunks, summary)
    )
    return data.get("html_url", "")


def review_check_payload(sha: str, total: int, reviewed: int, left: int) -> dict:
    """The gate check again on the commit a review pushed, so the pull request's head carries it.
    It succeeds once a person has decided every hunk the gate held back."""
    return {
        "name": CHECK_NAME,
        "head_sha": sha,
        "status": "completed",
        "conclusion": "action_required" if left else "success",
        "output": {
            "title": (
                f"{left} of {total} hunks still need review"
                if left
                else f"All {total} hunks resolved"
            ),
            "summary": (
                f"A reviewer committed {reviewed} resolution(s) on this commit; "
                + (f"{left} still keep conflict markers." if left else "none are left open.")
            ),
        },
    }


def post_review_check(token: str, repo: str, sha: str, total: int, reviewed: int, left: int) -> str:
    data = request(
        token,
        "POST",
        f"/repos/{repo}/check-runs",
        json=review_check_payload(sha, total, reviewed, left),
    )
    return data.get("html_url", "")


def ci_status(token: str, repo: str, sha: str) -> str:
    """The customer's CI verdict on `sha`, ignoring Forkward's own check: 'pending' while any run
    is unfinished, 'failure' if any finished run failed, 'success' when all passed, and 'none'
    when the repository has no checks at all."""
    data = request(
        token, "GET", f"/repos/{repo}/commits/{sha}/check-runs", params={"per_page": 100}
    )
    runs = [r for r in data.get("check_runs", []) if r.get("name") != CHECK_NAME]
    if not runs:
        return "none"
    if any(r.get("status") != "completed" for r in runs):
        return "pending"
    bad = {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}
    return "failure" if any(r.get("conclusion") in bad for r in runs) else "success"


def mark_ready(token: str, repo: str, number: int) -> None:
    """Take a draft pull request out of draft. REST cannot do this; GraphQL can."""
    pr = request(token, "GET", f"/repos/{repo}/pulls/{number}")
    if not pr.get("draft"):
        return
    with _client() as c:
        _check(
            c.post(
                "/graphql",
                headers=_headers(token),
                json={
                    "query": "mutation($id: ID!) { markPullRequestReadyForReview("
                    "input: {pullRequestId: $id}) { pullRequest { isDraft } } }",
                    "variables": {"id": pr["node_id"]},
                },
            ),
            "marking the pull request ready",
        )


def verify_webhook(body: bytes, signature: str | None, secret: str | None = None) -> bool:
    """Constant-time check of X-Hub-Signature-256. A missing secret rejects everything."""
    secret = secret if secret is not None else os.environ.get("GITHUB_WEBHOOK_SECRET")
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    want = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, signature.removeprefix("sha256="))
