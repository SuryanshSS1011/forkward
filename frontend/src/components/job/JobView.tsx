"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { AgentMark } from "@/components/AgentMark";
import type { JobDetail, Stage } from "@/lib/jobs";
import { HunkDetail, type ReviewFn } from "./HunkDetail";
import { HunkList } from "./HunkList";
import { StageTrack } from "./StageTrack";
import { agentState, headline, hunkState } from "./words";

const when = (ts: string) => new Date(ts).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

/**
 * One sync or preview: a sentence saying where it stands, the stages, and every conflict with its
 * proposals. The same view shows a live job (polled from Postgres) and the recorded sample.
 */
export function JobView({
  job,
  onReview,
  onApply,
  applyLabel,
  extraActions,
  note,
  hideStages,
  sample = false,
}: {
  job: JobDetail;
  onReview?: ReviewFn;
  onApply?: () => Promise<void>;
  applyLabel?: string;
  extraActions?: React.ReactNode;
  note?: React.ReactNode;
  /** Stages this run never had, such as the pull request and CI in a recording without one. */
  hideStages?: Stage[];
  /** A recorded run shown to anyone: no review or commit controls and no links into a workspace. */
  sample?: boolean;
}) {
  const hunks = job.hunks;
  const firstNeeds = useMemo(() => hunks.find((h) => hunkState(h) === "needs") ?? hunks[0], [hunks]);
  const [selected, setSelected] = useState<string | null>(firstNeeds?.id ?? null);
  useEffect(() => {
    if (!selected && firstNeeds) setSelected(firstNeeds.id);
  }, [firstNeeds, selected]);
  const current = hunks.find((h) => h.id === selected) ?? null;

  const accepted = hunks.filter((h) => hunkState(h) === "accepted").length;
  const undecided = hunks.filter((h) => hunkState(h) === "needs").length;
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const reviewOpen = !sample && job.kind === "sync" && ["waiting_ci", "waiting_approval"].includes(job.status);
  const models = Object.keys(job.summary.models ?? {});
  const committing = job.review_requested > job.review_applied;

  const apply = async () => {
    if (!onApply) return;
    setApplying(true);
    setApplyError(null);
    try {
      await onApply();
    } catch (e) {
      setApplyError(e instanceof Error ? e.message : "That did not start. Try again.");
    } finally {
      setApplying(false);
    }
  };

  // After a decision, move to the next conflict still waiting, so a review is one pass down the list.
  const review: ReviewFn | undefined = onReview
    ? async (h, decision, resolution) => {
        await onReview(h, decision, resolution);
        if (!decision) return;
        const next = hunks.find((x) => x.id !== h.id && hunkState(x) === "needs");
        if (next) setSelected(next.id);
      }
    : undefined;

  const select = (id: string) => {
    setSelected(id);
    if (window.matchMedia("(max-width: 1023px)").matches) document.getElementById("hunk-detail")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const [owner, name] = job.repo.split("/");
  return (
    <div className="wrap pb-10 pt-8">
      <header className="grid items-center gap-x-8 gap-y-4 md:grid-cols-[auto_1fr]">
        <div className="hidden md:block">
          <AgentMark state={agentState(job)} size={112} />
        </div>
        <div className="min-w-0">
          <p className="text-[15px] text-muted">
            {job.kind === "preview" ? "Preview of " : "Sync of "}
            <span className="font-mono text-[14px] text-ink">
              {owner}/<b className="font-semibold">{name}</b>
            </span>
            {" with "}
            <span className="font-mono text-[14px] text-ink">{job.upstream_ref}</span>
          </p>
          <h1 className="t-h1 mt-2 max-w-[24ch]">
            {reviewOpen && undecided === 0 && accepted > 0 && !committing
              ? `${accepted} ${accepted === 1 ? "fix" : "fixes"} accepted. Commit ${accepted === 1 ? "it" : "them"} to the branch.`
              : headline(job)}
          </h1>
          {job.status === "failed" && job.error && (
            <p role="alert" className="mt-3 max-w-[70ch] border-l-2 border-danger pl-3 text-[15px]">
              {job.error}
            </p>
          )}
          <div className="mt-5 flex flex-wrap items-center gap-3">
            {reviewOpen && onApply && (accepted > 0 || committing) && (
              <button className="btn btn-accent" disabled={applying || committing} onClick={apply}>
                {committing ? "Committing to the branch…" : (applyLabel ?? `Commit ${accepted} accepted ${accepted === 1 ? "hunk" : "hunks"} to the branch`)}
              </button>
            )}
            {job.pr_url && (
              <a className="btn btn-secondary" href={job.pr_url} target="_blank" rel="noreferrer">
                Open the pull request
              </a>
            )}
            {extraActions}
            {/* Times follow the viewer's timezone, which the server cannot know. */}
            <span className="t-label text-faint" suppressHydrationWarning>
              {sample ? "Recorded" : "Started"} {when(job.created_at)}
              {models.length > 0 && <> with {models.join(", ")}</>}
            </span>
          </div>
          {applyError && (
            <p role="alert" className="mt-2 t-label text-danger">
              {applyError}
            </p>
          )}
          {note && <div className="mt-3 max-w-[70ch] text-[14px] text-muted">{note}</div>}
        </div>
      </header>

      <div className="mt-8">
        <StageTrack job={job} hide={hideStages} />
      </div>

      {hunks.length > 0 ? (
        <div className="mt-8 grid gap-8 border-t border-line pt-6 lg:grid-cols-[320px_1fr]">
          <aside className="lg:sticky lg:top-24 lg:self-start">
            <HunkList hunks={hunks} selected={selected} onSelect={select} />
          </aside>
          <div id="hunk-detail" className="min-w-0 scroll-mt-24">
            {current && (
              <div key={current.id} className="fade-swap">
                <HunkDetail hunk={current} onReview={reviewOpen ? review : undefined} />
              </div>
            )}
          </div>
        </div>
      ) : (
        ["queued", "running"].includes(job.status) && (
          <p className="mt-8 border-t border-line pt-6 text-muted">Conflicts appear here as soon as the merge finds them.</p>
        )
      )}

      {job.events.length > 0 && (
        <details className="mt-10 border-t border-line pt-4">
          <summary className="cursor-pointer text-[15px] font-semibold">
            What happened{" "}
            <span className="font-normal text-muted">
              ({job.events.length} {job.events.length === 1 ? "step" : "steps"})
            </span>
          </summary>
          <ol className="mt-3 space-y-2">
            {job.events.map((e) => (
              <li key={e.id} className={`event event-${e.kind} grid grid-cols-[96px_1fr] gap-3 text-[14px]`}>
                <time className="tabular-nums text-faint" dateTime={e.ts} suppressHydrationWarning>
                  {new Date(e.ts).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" })}
                </time>
                <span>{e.message}</span>
              </li>
            ))}
          </ol>
        </details>
      )}
      {!sample && (
        <p className="mt-8 text-[14px]">
          <Link href="/repos" className="link">
            All repositories
          </Link>
        </p>
      )}
    </div>
  );
}
