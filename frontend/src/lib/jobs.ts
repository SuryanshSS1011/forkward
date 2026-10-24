/** Shapes the web app reads from /api/repos and /api/jobs. They mirror the jobs, hunks and
 * job_events tables that the Python worker writes (migrations 001 and 003). */

export type JobKind = "sync" | "preview";
export type JobStatus = "queued" | "running" | "waiting_ci" | "waiting_approval" | "applying" | "succeeded" | "failed" | "cancelled";
export type Stage = "fetch" | "merge" | "resolve" | "open_pr" | "ci" | "review" | "done";

export type JobSummary = {
  id: string;
  repo: string;
  kind: JobKind;
  status: JobStatus;
  stage: Stage | null;
  progress: { done?: number; total?: number };
  pr_url: string | null;
  summary: {
    hunks?: number;
    auto_applied?: number;
    needs_review?: number;
    clean_merge?: boolean;
    branch?: string;
    ci?: string;
    pr?: string;
  };
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type Candidate = { lines: string[]; sources: string[]; votes: number; label: string; chosen: boolean };

export type HunkRow = {
  id: string;
  file: string;
  idx: number;
  hunk_ref: string | null;
  decision: "auto" | "review" | "error" | null;
  confidence: number | null;
  source: string | null;
  reason: string | null;
  applied: boolean;
  review: "accepted" | "rejected" | null;
  resolution?: string | null;
  final_resolution?: string | null;
  review_commit?: string | null;
  detail?: {
    vendor?: string[];
    upstream?: string[];
    candidates?: Candidate[];
    classifier?: string | null;
    proposers?: number;
  };
};

export type JobEvent = { id: number; ts: string; stage: Stage | null; kind: "stage" | "info" | "warning" | "error" | "done" | "progress"; message: string; data: Record<string, unknown> };

export type JobDetail = JobSummary & {
  upstream_ref: string;
  base: string | null;
  branch: string | null;
  review_requested: number;
  review_applied: number;
  hunks: HunkRow[];
  events: JobEvent[];
};

export type RepoRow = {
  id: string;
  full_name: string;
  upstream: string | null;
  default_branch: string | null;
  schedule: string | null;
  account_login: string;
  latest: JobSummary | null;
};

export const ACTIVE: JobStatus[] = ["queued", "running", "applying"];
export const isActive = (s: JobStatus) => ACTIVE.includes(s);

/** owner/name@ref, the upstream format the Python side validates. */
export const UPSTREAM_RE = /^[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9._-]{1,100}@[A-Za-z0-9._\/-]{1,255}$/;
