"""Durable sync jobs on DBOS (MIT), checkpointed in the same Postgres as the product data.

A hosted sync is one workflow per job. Each step is checkpointed, so a worker that restarts
resumes where it stopped instead of re-running model calls or pushing twice:

    1. load the job and mark it running
    2. sync: merge, resolve through the gate, push a forkward/* branch, open a PR (draft while any
       hunk needs review) and post the Forkward gate check run
    3. record hunks and the result
    4. wait for the customer's CI on the merge commit (woken by the check_suite webhook, with a
       polling fallback), and record the verdict
    5. wait for the pull request to be merged or closed (woken by the pull_request webhook), then
       learn from what the reviewer kept

Builds never run on the hosted machine; the customer's own CI is the verifier. Scheduled syncs
are an hourly DBOS schedule that enqueues a job for every enabled repository whose interval has
passed. Run the worker with `python -m forkward worker`.

A preview job runs steps 1 to 3 without pushing. A review job commits the hunks a person accepted
in the web app onto the open sync branch.

Every job reports to Postgres as it goes: `jobs.stage` and `jobs.progress` for the live view, and
a short `job_events` log of stage changes and problems. The web app reads only these, so what a
customer sees is exactly what the worker did. The web app starts work by inserting a job row (or
bumping `review_requested`); the worker's claim loop picks it up within a couple of seconds.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

from dbos import DBOS

from forkward import db

QUEUE = "syncs"
REVIEW_QUEUE = "reviews"
MAX_HUNKS = int(os.environ.get("FORKWARD_MAX_HUNKS", "200"))
CI_WAIT_S = float(os.environ.get("FORKWARD_CI_WAIT_S", str(6 * 3600)))
CI_POLL_S = float(os.environ.get("FORKWARD_CI_POLL_S", "600"))
PR_WAIT_S = float(os.environ.get("FORKWARD_PR_WAIT_S", str(30 * 86400)))
INTERVALS = {"hourly": 3600, "daily": 86400, "weekly": 7 * 86400}

_DBOS = None


def config() -> dict:
    url = db.url()
    if not url:
        raise db.DatabaseError("DATABASE_URL is not set")
    cfg = {
        "name": "forkward",
        "system_database_url": url,
        "application_version": os.environ.get("FORKWARD_VERSION", "1"),
    }
    from forkward import telemetry

    # DBOS adds its workflow and step spans to the global provider telemetry installs.
    cfg["enable_otlp"] = telemetry.setup("forkward-worker")
    return cfg


def _dbos():
    """The DBOS instance, created once per process. Workflow functions are registered at import
    time below, before launch."""
    global _DBOS
    if _DBOS is None:
        _DBOS = DBOS(config=config())
    return _DBOS


def start() -> None:
    """Launch DBOS in this process: recover interrupted workflows and start consuming the sync
    queue."""
    _dbos()
    DBOS.launch()
    DBOS.register_queue(QUEUE, worker_concurrency=int(os.environ.get("FORKWARD_WORKERS", "2")))
    # Review commits are short and a person is waiting on them, so they never queue behind syncs.
    DBOS.register_queue(REVIEW_QUEUE, worker_concurrency=2)


# Progress reporting -----------------------------------------------------------------------------

STAGES = {
    "fetch_upstream": "fetch",
    "merge": "merge",
    "apply": "resolve",
    "open_pr": "open_pr",
}


def _n(k: int, word: str) -> str:
    return f"{k} {word}" + ("" if k == 1 else "s")


def log_event(job: dict, stage: str | None, kind: str, message: str, **data) -> None:
    """One row in the job's event log, and the job's current stage when `stage` is given."""
    with db.tenant(job["tenant_id"]) as cur:
        cur.execute(
            "INSERT INTO job_events (job_id, tenant_id, stage, kind, message, data) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (job["id"], job["tenant_id"], stage, kind, message[:500], json.dumps(data)),
        )
        if stage:
            cur.execute(
                "UPDATE jobs SET stage = %s, updated_at = now() WHERE id = %s", (stage, job["id"])
            )


class JobLog:
    """Store observer that turns a sync's run-log events into the job's stage, progress and a
    handful of event rows. Per-hunk decisions only move the progress counter, so the log stays
    a short story of the run rather than a stream of notifications."""

    def __init__(self, job: dict):
        self.job = job
        self.total = 0
        self.done = 0
        self._written = 0.0

    def set_total(self, n: int) -> None:
        self.total = n
        self._progress(force=True)
        log_event(self.job, "resolve", "stage", f"Resolving {n} conflict hunks", total=n)

    def _progress(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and self.done < self.total and now - self._written < 1.0:
            return
        self._written = now
        with db.tenant(self.job["tenant_id"]) as cur:
            cur.execute(
                "UPDATE jobs SET progress = %s, updated_at = now() WHERE id = %s",
                (json.dumps({"done": self.done, "total": self.total}), self.job["id"]),
            )

    def __call__(self, doc: dict) -> None:
        agent, kind = doc.get("agent"), doc.get("kind")
        if agent == "sync" and kind == "step":
            stage = STAGES.get(doc.get("task", ""))
            if stage and stage != "resolve":
                log_event(self.job, stage, "stage", doc.get("message", ""))
        elif agent == "gate" and kind == "decision":
            self.done += 1
            self._progress()
        elif agent == "sync" and kind == "alert" and doc.get("level") == "needs_review":
            log_event(self.job, None, "warning", doc.get("message", ""))


# Steps ------------------------------------------------------------------------------------------


def _owned(cur, repo: str, tenant_id: str) -> bool:
    """Whether the repository belongs to an active installation of this tenant. This is what
    entitles the hosted service to push to it."""
    cur.execute(
        "SELECT r.full_name FROM repositories r JOIN installations i "
        "ON i.id = r.installation_id WHERE lower(r.full_name) = lower(%s) "
        "AND r.tenant_id = %s AND r.enabled AND i.suspended_at IS NULL",
        (repo, tenant_id),
    )
    return cur.fetchone() is not None


@DBOS.step()
def load_job(job_id: str) -> dict:
    with db.system() as cur:
        cur.execute(
            "UPDATE jobs SET status = 'running', updated_at = now() WHERE id = %s "
            "RETURNING id, tenant_id, repository_id, repo, kind, upstream_ref, base, model",
            (job_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"no job {job_id}")
        owned = _owned(cur, row["repo"], row["tenant_id"])
    job = {**{k: str(v) if k == "id" else v for k, v in row.items()}, "owned": owned}
    log_event(job, "fetch", "stage", f"Started: {job['repo']} with {job['upstream_ref']}")
    return job


def job_branch(job_id: str) -> str:
    return f"forkward/sync-{job_id}"


def _run(job: dict, preview: bool) -> dict:
    """Merge and resolve, then (for a sync) push and open the PR, with billing reserved from the
    hunk count before any model call and settled however the run ends. A sync's branch is named
    after the job, so a retry after a crash finds a branch the earlier attempt pushed and fails
    safely instead of opening a second pull request. Units a crashed attempt reserved are returned
    first."""
    from dataclasses import asdict

    from forkward import billing, llm, sync

    if not job["owned"]:
        raise PermissionError(f"{job['repo']} is not in an active installation of this tenant")
    with db.system() as cur:
        cur.execute("SELECT name, plan FROM tenants WHERE id = %s", (job["tenant_id"],))
        t = cur.fetchone()
    customer = billing.Customer(f"job:{job['id']}", t["name"], t["plan"], job["tenant_id"])
    release_abandoned(job, customer)
    branch = None if preview else job_branch(job["id"])
    if branch and _branch_sha(job["repo"], branch, log=False):
        raise RuntimeError(
            f"{branch} is already on GitHub from an earlier attempt of this job; review or close "
            "its pull request there"
        )
    reservation = None
    log = JobLog(job)

    async def reserve(n: int) -> None:
        # Units come from the real hunk count and are taken before any model call. The job row
        # notes them until they are settled, so a crash cannot leak them.
        nonlocal reservation
        reservation = await asyncio.to_thread(
            billing.reserve, customer, billing.units_for(job.get("model"), n)
        )
        await asyncio.to_thread(_note_reservation, job, reservation)
        await asyncio.to_thread(log.set_total, n)

    async def go():
        with llm.use_model(job.get("model")):
            if preview:
                from forkward import github_app

                token = await asyncio.to_thread(github_app.token_for, job["repo"])
                return await sync.preview(
                    job["repo"],
                    job.get("base"),
                    job["upstream_ref"],
                    max_hunks=MAX_HUNKS,
                    on_hunks=reserve,
                    observer=log,
                    token=token,
                )
            return await sync.sync(
                job["repo"],
                job["upstream_ref"],
                job.get("base"),
                open_pr=True,
                app_push=True,
                max_hunks=MAX_HUNKS,
                on_hunks=reserve,
                observer=log,
                branch=branch,
            )

    res = None
    try:
        res = asyncio.run(go())
    finally:
        if reservation is not None:
            # A run that fails is not charged; the reservation goes back to the allowance.
            decided = sum(h.gate != "blocked" for h in res.hunks) if res else 0
            billing.settle(
                reservation,
                "preview" if preview else "open_sync_pr",
                billing.units_for(job.get("model"), decided),
                status="ok" if res else "failed",
                repo=job["repo"],
                job_id=job["id"],
            )
            _note_reservation(job, None)
    out = asdict(res)
    out["needs_review"] = res.needs_review
    out["auto_applied"] = res.auto_applied
    out["head_sha"] = None if preview else _branch_sha(job["repo"], res.branch)
    return out


@DBOS.step()
def run_sync(job: dict) -> dict:
    """The whole merge-resolve-push in one checkpointed step."""
    return _run(job, preview=False)


@DBOS.step()
def run_preview(job: dict) -> dict:
    """Merge and resolve in a throwaway worktree. Nothing is pushed."""
    return _run(job, preview=True)


def _note_reservation(job: dict, r) -> None:
    """Record (or, with None, clear) the units this job holds but has not settled yet."""
    with db.tenant(job["tenant_id"]) as cur:
        if r is None:
            cur.execute(
                "UPDATE jobs SET summary = summary - 'reserved' WHERE id = %s", (job["id"],)
            )
        else:
            cur.execute(
                "UPDATE jobs SET summary = summary || %s WHERE id = %s",
                (
                    json.dumps({"reserved": {"units": r.units, "month": r.month.isoformat()}}),
                    job["id"],
                ),
            )


def release_abandoned(job: dict, customer) -> None:
    """Return units an earlier, crashed attempt of this job reserved and never settled."""
    from datetime import date

    from forkward import billing

    with db.tenant(job["tenant_id"]) as cur:
        cur.execute("SELECT summary -> 'reserved' AS r FROM jobs WHERE id = %s", (job["id"],))
        row = cur.fetchone()
    held = row and row["r"]
    if not held:
        return
    r = billing.Reservation(customer, date.fromisoformat(held["month"]), int(held["units"]))
    tool = "preview" if job.get("kind") == "preview" else "open_sync_pr"
    billing.settle(r, tool, 0, status="abandoned", repo=job["repo"], job_id=job["id"])
    _note_reservation(job, None)


def _branch_sha(repo: str, branch: str, log: bool = True) -> str | None:
    from forkward import github_app

    try:
        token = github_app.token_for(repo)
        if not token:
            return None
        data = github_app.request(token, "GET", f"/repos/{repo}/branches/{branch}")
        return data["commit"]["sha"]
    except Exception as e:  # noqa: BLE001 - only the CI wait needs it, and it can poll later
        if log:
            print(f"[workflows] branch head lookup failed: {e}", file=sys.stderr)
        return None


@DBOS.step()
def record_result(job: dict, res: dict, preview: bool = False) -> None:
    import hashlib

    status = "succeeded" if preview or not res.get("pr_url") else "waiting_ci"
    summary = {
        k: res.get(k)
        for k in (
            "branch",
            "pr_url",
            "head_sha",
            "upstream_sha",
            "auto_applied",
            "needs_review",
            "strategy",
            "clean_merge",
        )
    }
    summary["hunks"] = len(res.get("hunks", []))
    with db.tenant(job["tenant_id"]) as cur:
        for i, h in enumerate(res.get("hunks", [])):
            cur.execute(
                "INSERT INTO hunks (job_id, tenant_id, file, idx, hunk_ref, conflict_sha256, "
                "decision, confidence, source, reason, resolution, applied, detail) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (job_id, file, idx) DO NOTHING",
                (
                    job["id"],
                    job["tenant_id"],
                    h["file"],
                    i,
                    h["hunk_id"],
                    h.get("key") or hashlib.sha256(h["hunk_id"].encode()).hexdigest(),
                    {"auto_apply": "auto", "needs_review": "review"}.get(h["gate"], "error"),
                    h["confidence"],
                    _source(h),
                    h["reason"][:500],
                    h["resolution"],
                    h["gate"] == "auto_apply" and not preview,
                    json.dumps(h.get("detail") or {}),
                ),
            )
        cur.execute(
            "UPDATE jobs SET status = %s, branch = %s, pr_url = %s, summary = %s, stage = %s, "
            "updated_at = now() WHERE id = %s",
            (
                status,
                None if preview else res.get("branch"),
                res.get("pr_url"),
                json.dumps(summary),
                "done" if status == "succeeded" else "ci",
                job["id"],
            ),
        )
        cur.execute(
            "INSERT INTO audit_events (tenant_id, actor, action, subject, detail) "
            "VALUES (%s, 'forkward-bot', %s, %s, %s)",
            (
                job["tenant_id"],
                "preview.finished" if preview else "sync.opened",
                job["repo"],
                json.dumps(summary),
            ),
        )
    n, auto = summary["hunks"], summary["auto_applied"] or 0
    if not n:
        msg = "The merge was clean; nothing needed resolving"
    elif preview:
        msg = f"Preview finished: {auto} of {n} hunks would apply automatically, {n - auto} would need review"
    else:
        msg = f"Opened the pull request: {auto} of {n} hunks applied automatically, {n - auto} for review"
    log_event(job, "done" if status == "succeeded" else "ci", "done" if preview else "info", msg)


@DBOS.step()
def anchor_decisions(job: dict, head_sha: str | None) -> dict | None:
    """Write the job's decision digest to Solana when SOLANA_KEYPAIR is set. A chain outage only
    costs the anchor, never the sync."""
    from forkward import solana

    if not solana.enabled():
        return None
    try:
        a = solana.anchor_job(job, head_sha)
    except Exception as e:  # noqa: BLE001 - tamper evidence is extra; the pull request is open
        log_event(job, None, "warning", f"Could not anchor the decisions on Solana: {e}"[:300])
        return None
    log_event(
        job,
        None,
        "info",
        f"Anchored the decisions on Solana {a['cluster']}",
        explorer=a["explorer"],
    )
    return a


def _source(h: dict) -> str:
    chosen = next((c for c in (h.get("detail") or {}).get("candidates", []) if c["chosen"]), None)
    return "+".join(chosen["sources"])[:300] if chosen else ""


@DBOS.step()
def poll_ci(repo: str, sha: str) -> str:
    from forkward import github_app

    return github_app.ci_status(github_app.token_for(repo), repo, sha)


@DBOS.step()
def set_status(job: dict, status: str, **summary) -> None:
    with db.tenant(job["tenant_id"]) as cur:
        cur.execute(
            "UPDATE jobs SET status = %s, summary = summary || %s, updated_at = now() "
            "WHERE id = %s",
            (status, json.dumps(summary), job["id"]),
        )


@DBOS.step()
def fail_job(job: dict, error: str) -> None:
    with db.system() as cur:
        cur.execute(
            "UPDATE jobs SET status = 'failed', error = %s, updated_at = now() WHERE id = %s",
            (error[:2000], job["id"]),
        )
    log_event(job, None, "error", error[:500])


@DBOS.step()
def note(job: dict, stage: str | None, kind: str, message: str) -> None:
    log_event(job, stage, kind, message)


CI_MESSAGES = {
    "success": "Your CI passed on the sync branch",
    "failure": "Your CI failed on the sync branch; check the pull request",
    "none": "This repository has no CI checks, so nothing verified the build",
    "timeout": "Your CI did not finish within the wait; check the pull request",
    "unknown": "Could not read CI status for the sync branch",
}


@DBOS.step()
def learn(pr_url: str) -> dict:
    from forkward import history

    return history.record_outcome(pr_url)


# Workflows --------------------------------------------------------------------------------------


@DBOS.workflow()
def sync_job(job_id: str) -> dict:
    job = load_job(job_id)
    try:
        res = run_sync(job)
    except Exception as e:  # noqa: BLE001 - the job row carries the failure for the customer
        fail_job(job, f"{type(e).__name__}: {e}")
        return {"status": "failed"}
    record_result(job, res)
    if not res.get("pr_url"):
        return {"status": "succeeded"}
    anchor_decisions(job, res.get("head_sha"))
    # The waits for CI and for the merge can last days, so they run in their own workflow outside
    # the queue. A queued workflow holds a concurrency slot until it returns, and a few open pull
    # requests would otherwise stop every new sync and review from starting.
    from dbos import SetWorkflowID

    with SetWorkflowID(watch_id(job_id)):
        DBOS.start_workflow(watch_job, job, res["pr_url"], res.get("head_sha"))
    return {"status": "opened", "pr_url": res["pr_url"]}


def watch_id(job_id: str) -> str:
    return f"watch-{job_id}"


@DBOS.workflow()
def watch_job(job: dict, pr_url: str, head_sha: str | None) -> dict:
    """Waits for the customer's CI on the sync branch, then for the pull request to be merged or
    closed, and learns from the merge. Woken by webhooks through notify_ci and notify_pr."""
    ci = wait_for_ci(job["repo"], head_sha)
    note(job, "review", "info" if ci in ("success", "none") else "warning", CI_MESSAGES[ci])
    set_status(job, "waiting_approval", ci=ci)
    msg = DBOS.recv("pr", timeout_seconds=PR_WAIT_S)
    if msg is None:
        set_status(job, "succeeded", pr="open")
        return {"status": "succeeded", "ci": ci}
    outcome = {"closed": True}
    if msg.get("merged"):
        try:
            outcome = learn(pr_url)
        except Exception as e:  # noqa: BLE001 - the merge happened; only the learning failed
            outcome = {"error": f"{type(e).__name__}: {e}"[:500]}
    note(
        job,
        "done",
        "done",
        "The pull request was merged; Forkward recorded what the reviewer kept"
        if msg.get("merged")
        else "The pull request was closed without merging",
    )
    set_status(
        job,
        "succeeded" if msg.get("merged") else "cancelled",
        pr="merged" if msg.get("merged") else "closed",
        outcome=outcome,
    )
    return {"status": "merged" if msg.get("merged") else "closed", "ci": ci}


@DBOS.workflow()
def preview_job(job_id: str) -> dict:
    job = load_job(job_id)
    try:
        res = run_preview(job)
    except Exception as e:  # noqa: BLE001 - the job row carries the failure for the customer
        fail_job(job, f"{type(e).__name__}: {e}")
        return {"status": "failed"}
    record_result(job, res, preview=True)
    return {"status": "succeeded"}


@DBOS.step()
def commit_reviews(job_id: str, request: int) -> dict:
    """Commit every accepted, not yet committed hunk of a sync job onto its branch."""
    from forkward import github_app, sync

    with db.system() as cur:
        cur.execute(
            "SELECT id, tenant_id, repo, branch, pr_url, summary, review_applied FROM jobs "
            "WHERE id = %s",
            (job_id,),
        )
        j = cur.fetchone()
        # Checked again here: the installation may have been removed or suspended since the
        # sync ran, and then nothing is pushed on the tenant's behalf.
        if not _owned(cur, j["repo"], j["tenant_id"]):
            raise PermissionError(f"{j['repo']} is no longer in an active installation")
        cur.execute(
            "SELECT id, file, hunk_ref, review, final_resolution, resolution FROM hunks "
            "WHERE job_id = %s AND decision <> 'auto' AND review_commit IS NULL "
            "AND hunk_ref LIKE '%%#%%' AND hunk_ref NOT LIKE '%%#file'",
            (job_id,),
        )
        open_hunks = cur.fetchall()
    job = {"id": str(j["id"]), "tenant_id": j["tenant_id"]}
    accepted = [h for h in open_hunks if h["review"] == "accepted"]
    if j["review_applied"] >= request or not accepted:
        return {"committed": 0}
    unresolved: dict[str, list[int]] = {}
    for h in open_hunks:
        f, _, n = h["hunk_ref"].rpartition("#")
        unresolved.setdefault(f, []).append(int(n))
    token = github_app.token_for(j["repo"])
    sha = sync.apply_review(
        j["repo"],
        j["branch"],
        j["summary"]["upstream_sha"],
        [
            {"hunk_ref": h["hunk_ref"], "resolution": h["final_resolution"] or h["resolution"]}
            for h in accepted
        ],
        unresolved,
        f"Apply {len(accepted)} reviewed resolution(s)",
        token,
    )
    left = len(open_hunks) - len(accepted)
    if left == 0 and j["pr_url"] and token:
        try:
            github_app.mark_ready(token, j["repo"], int(j["pr_url"].rstrip("/").rsplit("/", 1)[1]))
        except Exception as e:  # noqa: BLE001 - the commit landed; a draft flag is cosmetic
            print(f"[workflows] could not mark the PR ready: {e}", file=sys.stderr)
    log_event(
        job,
        "review",
        "info",
        f"Committed {_n(len(accepted), 'reviewed hunk')} to the branch"
        + (", and the pull request is ready to merge" if left == 0 else f"; {left} still open"),
        commit=sha,
    )
    with db.tenant(j["tenant_id"]) as cur:
        cur.execute(
            "UPDATE hunks SET review_commit = %s, applied = true WHERE id = ANY(%s)",
            (sha, [h["id"] for h in accepted]),
        )
        cur.execute(
            "UPDATE jobs SET review_applied = %s, summary = summary || %s, updated_at = now() "
            "WHERE id = %s",
            (request, json.dumps({"head_sha": sha, "needs_review": left}), j["id"]),
        )
    return {"committed": len(accepted), "left": left, "sha": sha}


@DBOS.workflow()
def review_job(job_id: str, request: int) -> dict:
    try:
        return commit_reviews(job_id, request)
    except Exception as e:  # noqa: BLE001 - shown on the job page; the reviewer can retry
        with db.system() as cur:
            cur.execute("SELECT tenant_id FROM jobs WHERE id = %s", (job_id,))
            tenant = cur.fetchone()["tenant_id"]
        note({"id": job_id, "tenant_id": tenant}, None, "error", f"Could not commit reviews: {e}")
        return {"error": str(e)}


@DBOS.step()
def clock() -> float:
    """Wall time as a step, so a replayed workflow sees the same deadline it set the first time."""
    return time.time()


def wait_for_ci(repo: str, sha: str | None) -> str:
    """Wait for CI on `sha`. A check_suite webhook only wakes the wait early, since one suite
    finishing says nothing about the others; the verdict always comes from the Checks API over
    every run but Forkward's own. An API error (say, the installation was removed) keeps waiting
    until the deadline. Returns success, failure, none or timeout."""
    if not sha:
        return "unknown"
    deadline = clock() + CI_WAIT_S
    # Check once right away: a repository without CI, or one whose checks already finished,
    # should not wait for a webhook that will never come.
    status = _poll_ci_safely(repo, sha)
    while status not in ("success", "failure", "none"):
        if clock() > deadline:
            return "timeout"
        DBOS.recv("ci", timeout_seconds=CI_POLL_S)
        status = _poll_ci_safely(repo, sha)
    return status


def _poll_ci_safely(repo: str, sha: str) -> str | None:
    try:
        return poll_ci(repo, sha)
    except Exception as e:  # noqa: BLE001 - retried on the next wake, then times out
        print(f"[workflows] CI status for {repo}@{sha[:12]} failed: {e}", file=sys.stderr)
        return None


@DBOS.workflow()
def scheduled_syncs(scheduled_at, context) -> None:
    enqueue_due()


@DBOS.step()
def enqueue_due() -> int:
    """Create a queued job for every enabled repository whose interval has passed since its last
    job. It only writes rows: a DBOS step cannot start workflows, so the claim loop starts them,
    exactly as it does for jobs the web app creates."""
    with db.system() as cur:
        cur.execute(
            "SELECT r.id, r.tenant_id, r.full_name, r.upstream, r.schedule, "
            "(SELECT max(created_at) FROM jobs j WHERE j.repository_id = r.id) AS last "
            "FROM repositories r JOIN installations i ON i.id = r.installation_id "
            "WHERE r.enabled AND r.upstream IS NOT NULL AND r.schedule IS NOT NULL "
            "AND i.suspended_at IS NULL"
        )
        due = [
            r
            for r in cur.fetchall()
            if r["schedule"] in INTERVALS
            and (
                r["last"] is None or time.time() - r["last"].timestamp() >= INTERVALS[r["schedule"]]
            )
        ]
    for r in due:
        create_job(r["tenant_id"], r["full_name"], r["upstream"], repository_id=r["id"], claim=True)
    return len(due)


# Entry points -----------------------------------------------------------------------------------


WORKFLOWS = {"sync": sync_job, "preview": preview_job}


def create_job(
    tenant_id: str,
    repo: str,
    upstream: str,
    base: str | None = None,
    model: str | None = None,
    repository_id: int | None = None,
    kind: str = "sync",
    claim: bool = False,
) -> str:
    """Insert a queued job. With claim=True the row is left for the claim loop to start, as the
    web app's rows are; otherwise the caller starts its workflow right away."""
    with db.tenant(tenant_id) as cur:
        cur.execute(
            "INSERT INTO jobs (tenant_id, repository_id, repo, kind, upstream_ref, base, model) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (tenant_id, repository_id, repo, kind, upstream, base, model),
        )
        job_id = str(cur.fetchone()["id"])
        if not claim:
            cur.execute(
                "UPDATE jobs SET workflow_id = %s WHERE id = %s", (f"{kind}-{job_id}", job_id)
            )
    return job_id


def submit(
    tenant_id: str,
    repo: str,
    upstream: str,
    base: str | None = None,
    model: str | None = None,
    repository_id: int | None = None,
) -> str:
    """Create a job row and enqueue its workflow."""
    job_id = create_job(tenant_id, repo, upstream, base, model, repository_id)
    _enqueue(sync_job, f"sync-{job_id}", job_id)
    return job_id


def _enqueue(fn, wid: str, *args, queue: str = QUEUE) -> None:
    if _DBOS is not None:
        from dbos import SetWorkflowID

        with SetWorkflowID(wid):
            DBOS.enqueue_workflow(queue, fn, *args)
    else:
        _client().enqueue(
            {"queue_name": queue, "workflow_name": _name(fn), "workflow_id": wid}, *args
        )


def claim() -> int:
    """Start work the web app asked for. A job row the web app inserted has no workflow yet; a
    sync job whose review_requested moved past review_applied wants its reviews committed. Both
    are claimed under row locks, and workflow ids are derived from the row, so two workers (or a
    restart) never start the same work twice."""
    started = 0
    with db.system() as cur:
        cur.execute(
            "SELECT id, kind FROM jobs WHERE status = 'queued' AND workflow_id IS NULL "
            "AND kind IN ('sync', 'preview') ORDER BY created_at LIMIT 20 FOR UPDATE SKIP LOCKED"
        )
        fresh = cur.fetchall()
        for r in fresh:
            cur.execute(
                "UPDATE jobs SET workflow_id = %s WHERE id = %s",
                (f"{r['kind']}-{r['id']}", r["id"]),
            )
        cur.execute(
            "SELECT id, review_requested FROM jobs WHERE review_requested > review_applied "
            "AND status IN ('waiting_ci', 'waiting_approval', 'succeeded') "
            "AND coalesce(summary->>'pr', 'open') = 'open' LIMIT 20"
        )
        reviews = cur.fetchall()
    for r in fresh:
        _enqueue(WORKFLOWS[r["kind"]], f"{r['kind']}-{r['id']}", str(r["id"]))
        started += 1
    for r in reviews:
        try:
            _enqueue(
                review_job,
                f"review-{r['id']}-{r['review_requested']}",
                str(r["id"]),
                r["review_requested"],
                queue=REVIEW_QUEUE,
            )
            started += 1
        except Exception as e:  # noqa: BLE001 - an id already used means it is already running
            print(f"[workflows] review enqueue skipped: {e}", file=sys.stderr)
    return started


def _name(fn) -> str:
    return getattr(fn, "__qualname__", fn.__name__)


_CLIENT = None


def _client():
    global _CLIENT
    if _CLIENT is None:
        from dbos import DBOSClient

        _CLIENT = DBOSClient(system_database_url=db.url())
    return _CLIENT


def _send(workflow_id: str, message: dict, topic: str) -> None:
    if _DBOS is not None:
        DBOS.send(workflow_id, message, topic)
    else:
        _client().send(workflow_id, message, topic)


def notify_ci(repo: str, sha: str, conclusion: str | None) -> int:
    """Wake every workflow waiting on CI for this commit. Called by the check_suite webhook."""
    status = "success" if conclusion in ("success", "neutral", "skipped") else "failure"
    with db.system() as cur:
        cur.execute(
            "SELECT id::text FROM jobs WHERE lower(repo) = lower(%s) "
            "AND summary->>'head_sha' = %s AND status = 'waiting_ci' AND kind = 'sync'",
            (repo, sha),
        )
        ids = [watch_id(r["id"]) for r in cur.fetchall()]
    for wid in ids:
        _send(wid, {"status": status, "conclusion": conclusion}, "ci")
    return len(ids)


def notify_pr(pr_url: str, merged: bool) -> int:
    """Wake the workflow that opened this pull request. Called by the pull_request webhook."""
    with db.system() as cur:
        cur.execute(
            "SELECT id::text FROM jobs WHERE pr_url = %s AND kind = 'sync' "
            "AND status IN ('waiting_ci', 'waiting_approval')",
            (pr_url,),
        )
        ids = [watch_id(r["id"]) for r in cur.fetchall()]
    for wid in ids:
        _send(wid, {"merged": merged}, "pr")
    return len(ids)


def job(tenant_id: str, job_id: str, after: int = 0, detail: bool = False) -> dict | None:
    """A job, its hunks and its events after `after`, for the MCP task handle and the web app."""
    with db.tenant(tenant_id) as cur:
        cur.execute(
            "SELECT id, repo, kind, status, stage, progress, upstream_ref, base, branch, pr_url, "
            "summary, error, review_requested, review_applied, created_at, updated_at "
            "FROM jobs WHERE id = %s",
            (job_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = "id, file, idx, hunk_ref, decision, confidence, source, reason, applied, review"
        if detail:
            cols += ", resolution, final_resolution, detail, review_commit"
        cur.execute(f"SELECT {cols} FROM hunks WHERE job_id = %s ORDER BY idx", (job_id,))
        row["hunks"] = [{**h, "id": str(h["id"])} for h in cur.fetchall()]
        cur.execute(
            "SELECT id, ts, stage, kind, message, data FROM job_events WHERE job_id = %s "
            "AND id > %s ORDER BY id LIMIT 200",
            (job_id, after),
        )
        row["events"] = cur.fetchall()
    row["id"] = str(row["id"])
    return row


def worker() -> int:
    """Run the DBOS worker: executes queued sync jobs, recovers interrupted ones, and runs the
    hourly schedule."""
    from forkward import llm

    llm.load_env()
    start()
    if not DBOS.get_schedule("scheduled-syncs"):
        DBOS.create_schedule(
            schedule_name="scheduled-syncs", workflow_fn=scheduled_syncs, schedule="0 * * * *"
        )
    print("forkward worker running", file=sys.stderr, flush=True)
    poll = float(os.environ.get("FORKWARD_CLAIM_POLL_S", "2"))
    try:
        while True:
            try:
                claim()
            except Exception as e:  # noqa: BLE001 - a database blip must not stop the worker
                print(f"[workflows] claim failed: {e}", file=sys.stderr)
            time.sleep(poll)
    except KeyboardInterrupt:
        return 0
