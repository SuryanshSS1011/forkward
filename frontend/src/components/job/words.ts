import type { AgentState } from "@/components/AgentMark";
import type { Candidate, HunkRow, JobDetail, JobSummary, Stage } from "@/lib/jobs";

/** Plain names for what produced a candidate. Rules are the deterministic operators. */
const RULES: Record<string, string> = {
  keep_vendor: "Keep fork rule",
  take_upstream: "Take upstream rule",
  vendor_then_upstream: "Fork then upstream rule",
  upstream_then_vendor: "Upstream then fork rule",
  upstream_delta_on_vendor: "Upstream change on fork rule",
  vendor_delta_on_upstream: "Fork change on upstream rule",
  upstream_delta_on_vendor_fuzzy: "Upstream change on fork rule",
  vendor_delta_on_upstream_fuzzy: "Fork change on upstream rule",
  empty: "Delete block rule",
  rerere: "Earlier merge you approved",
  tok_interleave: "Word-level merge rule",
  tok_vendor: "Word-level fork rule",
  tok_upstream: "Word-level upstream rule",
  tok_base: "Word-level base rule",
  tok_vendor_then_upstream: "Word-level fork then upstream rule",
  tok_upstream_then_vendor: "Word-level upstream then fork rule",
};
const MODELS: Record<string, string> = {
  vendor_first: "Fork-first model",
  upstream_fix_first: "Fix-first model",
  refactor_aware: "Refactor-aware model",
  minimal_diff: "Minimal-edit model",
  operator_picker: "Rule-picking model",
  zero_shot: "Plain model",
  judge: "Judge model",
};

export function sourceName(s: string): string {
  if (s.startsWith("model:")) return MODELS[s.slice(6)] ?? s.slice(6).replace(/_/g, " ");
  return RULES[s] ?? s.replace(/_/g, " ");
}

export const LABELS: Record<string, string> = {
  keep_vendor: "Keep your fork's side",
  take_upstream: "Take upstream's side",
  merge: "Combine both sides",
};

/** What a merge-class candidate does, named by the rule that produced it when there is one. */
const MERGE_TITLES: Record<string, string> = {
  vendor_then_upstream: "Your fork's lines, then upstream's",
  upstream_then_vendor: "Upstream's lines, then your fork's",
  empty: "Delete the block",
  upstream_delta_on_vendor: "Upstream's change applied to your fork",
  upstream_delta_on_vendor_fuzzy: "Upstream's change applied to your fork",
  vendor_delta_on_upstream: "Your fork's change applied to upstream",
  vendor_delta_on_upstream_fuzzy: "Your fork's change applied to upstream",
  tok_interleave: "Both sides merged word by word",
  tok_vendor_then_upstream: "Both sides merged word by word",
  tok_upstream_then_vendor: "Both sides merged word by word",
  rerere: "The resolution you approved last time",
};

export function candidateTitle(c: Candidate): string {
  if (c.label !== "merge") return LABELS[c.label] ?? "Another resolution";
  const rule = c.sources.find((s) => !s.startsWith("model:") && MERGE_TITLES[s]);
  return rule ? MERGE_TITLES[rule] : "Both sides combined by a model";
}

/** "Needs you" plus the reason, as one or two sentences. */
export function statusLine(stateLabel: string, reason: string | null | undefined, showReason: boolean): string {
  if (!showReason || !reason) return `${stateLabel}.`;
  const r = reason.trim().replace(/\.$/, "");
  return `${stateLabel}. ${r.charAt(0).toUpperCase()}${r.slice(1)}.`;
}

export const STAGES: { key: Stage; label: string }[] = [
  { key: "fetch", label: "Fetch upstream" },
  { key: "merge", label: "Merge" },
  { key: "resolve", label: "Resolve" },
  { key: "open_pr", label: "Pull request" },
  { key: "ci", label: "Your CI" },
  { key: "review", label: "Review" },
  { key: "done", label: "Done" },
];

export const PREVIEW_STAGES = STAGES.filter((s) => !["open_pr", "ci", "review"].includes(s.key));

export type HunkState = "auto" | "needs" | "accepted" | "rejected" | "committed" | "error";

export function hunkState(h: HunkRow): HunkState {
  if (h.decision === "auto") return "auto";
  if (h.decision === "error" || h.decision === null) return "error";
  if (h.review_commit) return "committed";
  if (h.review === "accepted") return "accepted";
  if (h.review === "rejected") return "rejected";
  return "needs";
}

export const HUNK_STATE_LABEL: Record<HunkState, string> = {
  auto: "Applied automatically",
  needs: "Needs you",
  accepted: "Accepted, not committed yet",
  rejected: "Left for you to fix on GitHub",
  committed: "Committed to the branch",
  error: "Could not be resolved",
};

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** One sentence that says where the job is and what, if anything, it needs from the reader. */
export function headline(job: JobSummary | JobDetail): string {
  const s = job.summary ?? {};
  const n = s.hunks ?? job.progress?.total ?? 0;
  const auto = s.auto_applied ?? 0;
  const review = s.needs_review ?? n - auto;
  switch (job.status) {
    case "queued":
      return "Waiting for a worker to pick this up.";
    case "running":
      if (job.stage === "resolve" && job.progress?.total)
        return `Resolving conflicts, ${job.progress.done ?? 0} of ${job.progress.total} done.`;
      if (job.stage === "merge") return "Merging upstream into a new branch.";
      if (job.stage === "open_pr") return "Pushing the branch and opening the pull request.";
      return "Fetching upstream.";
    case "failed":
      return "This run stopped before it finished.";
    case "cancelled":
      return "The pull request was closed without merging.";
    default:
      break;
  }
  if (s.clean_merge && !n) return "Clean merge. There was nothing to resolve.";
  // With hunks loaded, count what the reviewer already committed, so the sentence credits them.
  const byYou = "hunks" in job ? job.hunks.filter((h) => h.review_commit).length : 0;
  if (!n) return "Nothing needed resolving.";
  const head = `${plural(n, "conflict")}: ${auto} applied automatically`;
  if (job.kind === "preview") return `${head}, ${review} would need you.`;
  if (job.status === "succeeded" && s.pr === "merged") return `${head}. The pull request is merged.`;
  if (review) return `${head}, ${review} ${review === 1 ? "needs" : "need"} you.`;
  if (s.ci === "failure") return `${head}. Your CI failed on the branch.`;
  if (job.status === "succeeded") return `${head}.`;
  return byYou ? `${head}, ${byYou} resolved by you. Ready to merge.` : `${head}. Ready to merge.`;
}

/** What the merge agent shows for a job. */
export function agentState(job: JobSummary): AgentState {
  if (job.status === "failed") return "failed";
  if (["queued", "running", "applying"].includes(job.status)) return "working";
  if (job.status === "cancelled") return "idle";
  const open = job.summary?.needs_review ?? 0;
  // A sync is verified once nothing is left for a person; a finished preview that found hunks
  // needing someone simply rests, since nothing was pushed.
  if (job.kind === "preview" && job.status === "succeeded") return open ? "idle" : "verified";
  // A sync shows verified only when nothing is left for a person and its CI did not fail.
  if (open || job.summary?.ci === "failure") return "waiting";
  return "verified";
}

/** Removes the indentation every line shares, so deeply nested code fits its pane. Only used for
 * display; the editable resolution keeps the file's exact indentation. */
export function dedent(lines: string[]): string[] {
  const widths = lines.filter((l) => l.trim()).map((l) => l.match(/^[\t ]*/)![0].length);
  const cut = widths.length ? Math.min(...widths) : 0;
  return cut ? lines.map((l) => l.slice(Math.min(cut, l.match(/^[\t ]*/)![0].length))) : lines;
}
