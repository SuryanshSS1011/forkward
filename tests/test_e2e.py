"""The whole hosted flow on one machine (needs TEST_DATABASE_URL): the web app inserts a job, the
DBOS worker claims it, merges, resolves through the gate, pushes a forkward/* branch and opens a
pull request on a fake GitHub, and a reviewer's accepted hunk is committed back to the branch."""

import time

import pytest

from e2e import harness
from forkward import db, github_app, llm, search, store, sync, workflows

TENANT = "t_e2e00000000000a"


@pytest.fixture
def stack(pg, tmp_path, monkeypatch):
    from dbos import DBOS

    root = harness.make_repos(tmp_path / "git")
    gh, model = harness.FakeGitHub(root).start(), harness.StubModel().start()
    runs = tmp_path / "runs"
    for k, v in harness.environment(root, gh, model, runs).items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("LUMINAL_API_KEY", raising=False)
    monkeypatch.delenv("FORKWARD_HOSTED", raising=False)
    monkeypatch.setattr(llm, "load_env", lambda path=None: None)
    monkeypatch.setattr(sync, "GIT_URL", f"file://{root}")
    monkeypatch.setattr(sync, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(sync, "RUNS", runs)
    monkeypatch.setattr(store, "RUNS", runs)
    monkeypatch.setattr(search, "FROZEN", runs / "frozen.json")
    monkeypatch.setattr(github_app, "API", gh.url)
    monkeypatch.setattr(workflows, "CI_POLL_S", 1.0)
    monkeypatch.setattr(workflows, "PR_WAIT_S", 60.0)
    # One sync slot, so a pull request that is waiting for review must not hold it.
    monkeypatch.setenv("FORKWARD_WORKERS", "1")
    github_app._TOKENS.clear()
    github_app._IDENTITY.clear()
    harness.seed_tenant(TENANT)
    with db.system() as cur:
        cur.execute("DROP SCHEMA IF EXISTS dbos CASCADE")
    workflows._DBOS = None
    workflows.start()
    yield root, gh, model
    DBOS.destroy(destroy_registry=False)
    workflows._DBOS = None
    gh.stop()
    model.stop()


def web_inserts_job(kind: str) -> str:
    """What frontend/src/lib/server/jobs.ts startJob does."""
    with db.tenant(TENANT) as cur:
        cur.execute(
            "INSERT INTO jobs (tenant_id, repository_id, repo, kind, upstream_ref, base) "
            "VALUES (%s, 501, %s, %s, %s, 'main') RETURNING id::text",
            (TENANT, harness.FORK, kind, harness.UPSTREAM_REF),
        )
        return cur.fetchone()["id"]


def wait(job_id: str, statuses: set[str], timeout: float = 60) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        workflows.claim()
        row = workflows.job(TENANT, job_id, detail=True)
        if row["status"] in statuses:
            return row
        time.sleep(0.3)
    raise AssertionError(f"job stuck at {row['status']} / {row['stage']}: {row['error']}")


def test_preview_reports_stages_progress_and_hunk_detail_without_pushing(stack):
    _, gh, _ = stack
    job_id = web_inserts_job("preview")
    row = wait(job_id, {"succeeded", "failed"})
    assert row["status"] == "succeeded", row["error"]
    assert row["stage"] == "done"
    assert row["progress"] == {"done": 3, "total": 3}
    assert {h["decision"] for h in row["hunks"]} == {"auto", "review"}
    review = next(h for h in row["hunks"] if h["decision"] == "review")
    assert review["detail"]["vendor"] and review["detail"]["upstream"]
    assert any(c["chosen"] for c in review["detail"]["candidates"])
    stages = [e["stage"] for e in row["events"] if e["kind"] == "stage"]
    assert stages[:3] == ["fetch", "fetch", "merge"] and "resolve" in stages
    assert row["events"][-1]["kind"] == "done"
    assert len(row["events"]) < 12  # a short story, not a notification per hunk
    assert gh.pulls == [] and not row["branch"]


def test_sync_opens_a_draft_pr_and_commits_an_accepted_review(stack):
    root, gh, _ = stack
    job_id = web_inserts_job("sync")
    row = wait(job_id, {"waiting_approval", "failed"})
    assert row["status"] == "waiting_approval", row["error"]
    assert len(gh.pulls) == 1 and gh.pulls[0]["draft"] is True
    # The open pull request waits outside the queue, so the single sync slot is free again.
    assert wait(web_inserts_job("preview"), {"succeeded", "failed"})["status"] == "succeeded"
    assert gh.check_runs[0]["conclusion"] == "action_required"
    branch = row["branch"]
    review = [h for h in row["hunks"] if h["decision"] == "review"]
    assert review
    target = review[0]
    before = harness.branch_file(root, harness.FORK, branch, target["file"])
    assert "<<<<<<<" in before

    with db.tenant(TENANT) as cur:
        for h in review:
            cur.execute(
                "UPDATE hunks SET review = 'accepted', final_resolution = %s WHERE id = %s",
                ("\tedited_by_reviewer(dev);" if h is target else None, h["id"]),
            )
        cur.execute(
            "UPDATE jobs SET review_requested = review_requested + 1 WHERE id = %s", (job_id,)
        )
    deadline = time.time() + 60
    while time.time() < deadline:
        workflows.claim()
        row = workflows.job(TENANT, job_id, detail=True)
        if row["review_applied"] == 1:
            break
        time.sleep(0.3)
    assert row["review_applied"] == 1, [e["message"] for e in row["events"]]
    after = harness.branch_file(root, harness.FORK, branch, target["file"])
    assert "<<<<<<<" not in after and "edited_by_reviewer(dev);" in after
    assert all(h["review_commit"] for h in row["hunks"] if h["decision"] == "review")
    assert gh.pulls[0]["draft"] is False  # every hunk is resolved, so the PR is ready
    # The gate check follows the branch head, so the pull request still shows it.
    head = row["summary"]["head_sha"]
    assert gh.check_runs[-1]["head_sha"] == head
    assert gh.check_runs[-1]["conclusion"] == "success"
    assert "ready to merge" in row["events"][-1]["message"]
