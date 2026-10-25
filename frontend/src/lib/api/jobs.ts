import type { JobDetail, JobKind, JobSummary, RepoRow } from "@/lib/jobs";

/** Fetches JSON and turns a non-2xx answer into an Error carrying the server's own message. */
async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { cache: "no-store", ...init, headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) } });
  } catch {
    throw new Error("Forkward is not reachable. Check your connection and try again.");
  }
  const body = (await res.json().catch(() => ({}))) as { error?: string };
  if (!res.ok) {
    const err = new Error(body.error ?? `The request failed (${res.status}).`) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return body as T;
}

export const listRepos = () => call<{ repositories: RepoRow[] }>("/api/repos").then((b) => b.repositories);
export const listJobs = () => call<{ jobs: JobSummary[] }>("/api/jobs").then((b) => b.jobs);
export const getJob = (id: string, after = 0) => call<JobDetail>(`/api/jobs/${id}?after=${after}`);
export const startJob = (repoId: string, kind: JobKind) =>
  call<{ job: string }>(`/api/repos/${repoId}/jobs`, { method: "POST", body: JSON.stringify({ kind }) }).then((b) => b.job);
export const saveRepo = (repoId: string, upstream: string | null, schedule: string | null) =>
  call<{ ok: true }>(`/api/repos/${repoId}`, { method: "PATCH", body: JSON.stringify({ upstream, schedule }) });
export const removeRepo = (repoId: string) => call<{ ok: true }>(`/api/repos/${repoId}`, { method: "DELETE" });
export const reviewHunk = (jobId: string, hunkId: string, decision: "accepted" | "rejected" | null, resolution?: string) =>
  call<{ ok: true }>(`/api/jobs/${jobId}/hunks/${hunkId}`, { method: "POST", body: JSON.stringify({ decision, resolution }) });
export const applyReviews = (jobId: string) => call<{ ok: true }>(`/api/jobs/${jobId}/apply`, { method: "POST" });
