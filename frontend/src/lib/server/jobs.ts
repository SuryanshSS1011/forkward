import type { PoolClient } from "pg";
import type { JobDetail, JobKind, JobSummary, RepoRow } from "@/lib/jobs";
import { PLANS, isPlanId } from "@/lib/plans";
import { withTenant } from "./db";

const SUMMARY_COLS = "j.id::text, j.repo, j.kind, j.status, j.stage, j.progress, j.pr_url, j.summary, j.error, j.created_at, j.updated_at";

export async function listRepos(tenantId: string): Promise<RepoRow[]> {
  return withTenant(tenantId, async (db) => {
    const { rows } = await db.query(
      `SELECT r.id::text, r.full_name, r.upstream, r.default_branch, r.schedule, i.account_login,
              (SELECT row_to_json(x) FROM (SELECT ${SUMMARY_COLS} FROM jobs j WHERE j.repository_id = r.id
                ORDER BY j.created_at DESC LIMIT 1) x) AS latest
         FROM repositories r JOIN installations i ON i.id = r.installation_id
        WHERE r.tenant_id = $1 AND r.enabled ORDER BY lower(r.full_name)`,
      [tenantId],
    );
    return rows as RepoRow[];
  });
}

export async function listJobs(tenantId: string, limit = 30): Promise<JobSummary[]> {
  return withTenant(tenantId, async (db) => {
    const { rows } = await db.query(`SELECT ${SUMMARY_COLS} FROM jobs j WHERE j.tenant_id = $1 ORDER BY j.created_at DESC LIMIT $2`, [tenantId, limit]);
    return rows as JobSummary[];
  });
}

export async function updateRepo(tenantId: string, id: string, upstream: string | null, schedule: string | null): Promise<boolean> {
  return withTenant(tenantId, async (db) => {
    const r = await db.query("UPDATE repositories SET upstream = $3, schedule = $4, updated_at = now() WHERE id = $1 AND tenant_id = $2", [
      id,
      tenantId,
      upstream,
      schedule,
    ]);
    return r.rowCount === 1;
  });
}

/** Takes a fork out of Forkward: it stops syncing and leaves the list. Adding it again on GitHub
 * brings it back, with no upstream until one is set. */
export async function removeRepo(tenantId: string, id: string): Promise<boolean> {
  return withTenant(tenantId, async (db) => {
    const r = await db.query(
      "UPDATE repositories SET enabled = false, upstream = NULL, schedule = NULL, updated_at = now() WHERE id = $1 AND tenant_id = $2 AND enabled",
      [id, tenantId],
    );
    return r.rowCount === 1;
  });
}

export type StartResult = { job: string } | { error: string; status: number };

/** Inserts a queued job. The worker's claim loop gives it a workflow within a couple of seconds.
 * One active job per repository at a time, so a double click never starts two syncs. */
export async function startJob(tenantId: string, repoId: string, kind: JobKind, userId: string): Promise<StartResult> {
  return withTenant(tenantId, async (db) => {
    const { rows } = await db.query<{ full_name: string; upstream: string | null; default_branch: string | null }>(
      "SELECT full_name, upstream, default_branch FROM repositories WHERE id = $1 AND tenant_id = $2 AND enabled FOR UPDATE",
      [repoId, tenantId],
    );
    const repo = rows[0];
    if (!repo) return { error: "No such repository in your workspace.", status: 404 };
    if (!repo.upstream) return { error: "Set the upstream this fork follows first.", status: 409 };
    const busy = await db.query("SELECT 1 FROM jobs WHERE repository_id = $1 AND status IN ('queued', 'running', 'applying')", [repoId]);
    if (busy.rowCount) return { error: "A job for this repository is already running.", status: 409 };
    // The exact cost depends on how many conflicts the merge hits, which only the worker learns, so
    // this refuses only a workspace with nothing left rather than letting the job fail later.
    if ((await unitsLeft(db, tenantId)) <= 0) {
      return { error: "This workspace has used all of this month's units. Usage and plan shows when they reset.", status: 402 };
    }
    const job = await db.query<{ id: string }>(
      "INSERT INTO jobs (tenant_id, repository_id, repo, kind, upstream_ref, base) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id::text",
      [tenantId, repoId, repo.full_name, kind, repo.upstream, repo.default_branch],
    );
    await audit(db, tenantId, userId, kind === "sync" ? "sync.requested" : "preview.requested", repo.full_name, { job: job.rows[0].id });
    return { job: job.rows[0].id };
  });
}

export async function getJob(tenantId: string, id: string, after: number): Promise<JobDetail | null> {
  return withTenant(tenantId, async (db) => {
    const { rows } = await db.query(
      `SELECT ${SUMMARY_COLS}, j.upstream_ref, j.base, j.branch, j.review_requested, j.review_applied FROM jobs j WHERE j.id = $1 AND j.tenant_id = $2`,
      [id, tenantId],
    );
    if (!rows[0]) return null;
    const hunks = await db.query(
      `SELECT id::text, file, idx, hunk_ref, decision, confidence, source, reason, applied, review,
              resolution, final_resolution, detail, review_commit
         FROM hunks WHERE job_id = $1 ORDER BY idx`,
      [id],
    );
    const events = await db.query("SELECT id::int, ts, stage, kind, message, data FROM job_events WHERE job_id = $1 AND id > $2 ORDER BY id LIMIT 200", [
      id,
      after,
    ]);
    return { ...(rows[0] as JobSummary), ...rows[0], hunks: hunks.rows, events: events.rows } as JobDetail;
  });
}

export type ReviewInput = { decision: "accepted" | "rejected" | null; resolution?: string };

/** Records a reviewer's decision on one hunk that needs review. An edited resolution is kept as
 * final_resolution; committing happens when the reviewer asks for it (requestApply). */
export async function reviewHunk(
  tenantId: string,
  jobId: string,
  hunkId: string,
  input: ReviewInput,
  userId: string,
): Promise<{ ok: true } | { error: string; status: number }> {
  return withTenant(tenantId, async (db) => {
    const { rows } = await db.query<{ decision: string; review_commit: string | null; kind: string; status: string; repo: string }>(
      `SELECT h.decision, h.review_commit, j.kind, j.status, j.repo FROM hunks h JOIN jobs j ON j.id = h.job_id
        WHERE h.id = $1 AND h.job_id = $2 AND h.tenant_id = $3 FOR UPDATE OF h`,
      [hunkId, jobId, tenantId],
    );
    const h = rows[0];
    if (!h) return { error: "No such hunk.", status: 404 };
    if (h.decision === "auto") return { error: "This hunk was applied automatically.", status: 409 };
    if (h.review_commit) return { error: "This hunk is already committed to the branch.", status: 409 };
    await db.query(`UPDATE hunks SET review = $2, final_resolution = $3, approved_by = $4, reviewed_at = now() WHERE id = $1`, [
      hunkId,
      input.decision,
      input.decision === "accepted" ? (input.resolution ?? null) : null,
      input.decision ? userId : null,
    ]);
    await audit(db, tenantId, userId, `hunk.${input.decision ?? "cleared"}`, h.repo, { job: jobId, hunk: hunkId, edited: input.resolution != null });
    return { ok: true };
  });
}

/** Asks the worker to commit every accepted hunk of a sync job to its branch. */
export async function requestApply(
  tenantId: string,
  jobId: string,
  userId: string,
): Promise<{ ok: true; request: number } | { error: string; status: number }> {
  return withTenant(tenantId, async (db) => {
    const { rows } = await db.query<{ kind: string; status: string; repo: string; pending: number }>(
      `SELECT j.kind, j.status, j.repo,
              (SELECT count(*)::int FROM hunks h WHERE h.job_id = j.id AND h.review = 'accepted' AND h.review_commit IS NULL) AS pending
         FROM jobs j WHERE j.id = $1 AND j.tenant_id = $2 FOR UPDATE`,
      [jobId, tenantId],
    );
    const j = rows[0];
    if (!j) return { error: "No such job.", status: 404 };
    if (j.kind !== "sync") return { error: "A preview has no branch to commit to. Run a sync instead.", status: 409 };
    if (!["waiting_ci", "waiting_approval"].includes(j.status)) return { error: "This sync's pull request is no longer open.", status: 409 };
    if (!j.pending) return { error: "Accept at least one hunk first.", status: 409 };
    const r = await db.query<{ review_requested: number }>(
      "UPDATE jobs SET review_requested = review_requested + 1, updated_at = now() WHERE id = $1 RETURNING review_requested",
      [jobId],
    );
    await db.query("INSERT INTO job_events (job_id, tenant_id, stage, kind, message) VALUES ($1, $2, 'review', 'info', $3)", [
      jobId,
      tenantId,
      `Committing ${j.pending} reviewed ${j.pending === 1 ? "hunk" : "hunks"} to the branch`,
    ]);
    await audit(db, tenantId, userId, "review.apply", j.repo, { job: jobId, hunks: j.pending });
    return { ok: true, request: r.rows[0].review_requested };
  });
}

/** Units left this month, computed as /api/billing/usage and billing.usage() in Python do: the plan's
 * cap minus the larger of units charged and units reserved by syncs still running. */
async function unitsLeft(db: PoolClient, tenantId: string): Promise<number> {
  const { rows } = await db.query<{ plan: string; used: string; reserved: number | null }>(
    `SELECT t.plan,
            (SELECT coalesce(sum(units), 0) FROM usage_events e
              WHERE e.tenant_id = t.id AND e.ts >= date_trunc('month', now() AT TIME ZONE 'UTC')) AS used,
            (SELECT q.used FROM usage_quota q
              WHERE q.tenant_id = t.id AND q.month = date_trunc('month', now() AT TIME ZONE 'UTC')::date) AS reserved
       FROM tenants t WHERE t.id = $1`,
    [tenantId],
  );
  const r = rows[0];
  const plan = isPlanId(r?.plan) ? r.plan : "trial";
  return PLANS[plan].monthly_units - Math.max(Number(r?.used ?? 0), r?.reserved ?? 0);
}

async function audit(db: PoolClient, tenantId: string, actor: string, action: string, subject: string, detail: object) {
  await db.query("INSERT INTO audit_events (tenant_id, actor, action, subject, detail) VALUES ($1, $2, $3, $4, $5)", [
    tenantId,
    actor,
    action,
    subject,
    JSON.stringify(detail),
  ]);
}
