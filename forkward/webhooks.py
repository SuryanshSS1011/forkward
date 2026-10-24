"""GitHub App webhook handling. The HTTP route (in mcp_server) verifies the signature and calls
`handle`, which keeps installations and repositories in step with GitHub and wakes the durable
sync workflows waiting on CI or on a pull request.

An installation is linked to a tenant only by the web app's Setup URL callback, which checks that
the signed-in user can see it. Webhooks therefore never create a tenant link: events for an
installation that no tenant has claimed are acknowledged and ignored.
"""

from __future__ import annotations

import json
import os

from forkward import db

HANDLED = {"installation", "installation_repositories", "check_suite", "pull_request", "ping"}


def _audit(cur, tenant: str, action: str, subject: str, detail: dict) -> None:
    cur.execute(
        "INSERT INTO audit_events (tenant_id, actor, action, subject, detail) "
        "VALUES (%s, 'github', %s, %s, %s)",
        (tenant, action, subject, json.dumps(detail)),
    )


def handle(event: str, payload: dict) -> dict:
    """Apply one verified webhook delivery. Returns a small summary for logs and tests."""
    if event not in HANDLED:
        return {"ignored": event}
    if event == "ping":
        return {"ok": True}
    inst = (payload.get("installation") or {}).get("id")
    if not inst:
        return {"ignored": "no installation"}
    with db.system() as cur:
        cur.execute("SELECT tenant_id FROM installations WHERE id = %s", (inst,))
        row = cur.fetchone()
        if row is None:
            return {"ignored": "unclaimed installation"}
        tenant = row["tenant_id"]
        out = HANDLERS[event](cur, tenant, inst, payload.get("action", ""), payload)
    # Workflow notifications go out after the transaction commits.
    wake = out.pop("_wake", None)
    if wake:
        from forkward import workflows

        if wake[0] == "ci":
            workflows.notify_ci(*wake[1:])
        else:
            workflows.notify_pr(*wake[1:])
    return out


def _installation(cur, tenant: str, inst: int, action: str, payload: dict) -> dict:
    if action == "deleted":
        cur.execute("DELETE FROM installations WHERE id = %s", (inst,))
    elif action in ("suspend", "unsuspend"):
        cur.execute(
            "UPDATE installations SET suspended_at = CASE WHEN %s THEN now() END, "
            "updated_at = now() WHERE id = %s",
            (action == "suspend", inst),
        )
    _audit(cur, tenant, f"installation.{action}", str(inst), {})
    return {"tenant": tenant, "installation": action}


def _repositories(cur, tenant: str, inst: int, action: str, payload: dict) -> dict:
    added = payload.get("repositories_added") or []
    removed = payload.get("repositories_removed") or []
    for r in added:
        cur.execute(
            "INSERT INTO repositories (id, installation_id, tenant_id, full_name) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET "
            "installation_id = excluded.installation_id, tenant_id = excluded.tenant_id, "
            "full_name = excluded.full_name, enabled = true, updated_at = now()",
            (r["id"], inst, tenant, r["full_name"]),
        )
    for r in removed:
        cur.execute(
            "DELETE FROM repositories WHERE id = %s AND installation_id = %s", (r["id"], inst)
        )
    _audit(
        cur,
        tenant,
        "installation.repositories",
        str(inst),
        {"added": [r["full_name"] for r in added], "removed": [r["full_name"] for r in removed]},
    )
    return {"tenant": tenant, "added": len(added), "removed": len(removed)}


def _check_suite(cur, tenant: str, inst: int, action: str, payload: dict) -> dict:
    """The customer's CI finished on a commit."""
    suite = payload.get("check_suite") or {}
    if action != "completed" or not suite.get("head_sha"):
        return {"ignored": f"check_suite.{action}"}
    if str((suite.get("app") or {}).get("id")) == os.environ.get("GITHUB_APP_ID"):
        return {"ignored": "own check suite"}
    repo = (payload.get("repository") or {}).get("full_name", "")
    sha, conclusion = suite["head_sha"], suite.get("conclusion")
    _audit(cur, tenant, "ci.completed", repo, {"sha": sha, "conclusion": conclusion})
    return {"tenant": tenant, "ci": conclusion, "sha": sha, "_wake": ("ci", repo, sha, conclusion)}


def _pull_request(cur, tenant: str, inst: int, action: str, payload: dict) -> dict:
    """A Forkward pull request was merged or closed."""
    pr = payload.get("pull_request") or {}
    if action != "closed" or not (pr.get("head") or {}).get("ref", "").startswith("forkward/"):
        return {"ignored": f"pull_request.{action}"}
    merged, url = bool(pr.get("merged")), pr.get("html_url", "")
    _audit(
        cur,
        tenant,
        "pr.merged" if merged else "pr.closed",
        url,
        {"merged_by": (pr.get("merged_by") or {}).get("login")},
    )
    return {"tenant": tenant, "pr": "merged" if merged else "closed", "_wake": ("pr", url, merged)}


HANDLERS = {
    "installation": _installation,
    "installation_repositories": _repositories,
    "check_suite": _check_suite,
    "pull_request": _pull_request,
}
