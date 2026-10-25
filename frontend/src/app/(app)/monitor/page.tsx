"use client";
import Link from "next/link";
import { useMe } from "@/components/useMe";
import { useEffect, useState } from "react";
import { HunkMark } from "@/components/job/HunkList";
import { headline } from "@/components/job/words";
import { listJobs } from "@/lib/api/jobs";
import { isActive, type JobSummary } from "@/lib/jobs";

const time = (ts: string) => new Date(ts).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

function dayLabel(ts: string): string {
  const d = new Date(ts);
  const today = new Date();
  const days = Math.round((new Date(today.toDateString()).getTime() - new Date(d.toDateString()).getTime()) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}

/** The run's outcome as a few short chips instead of a sentence. */
function outcome(j: JobSummary): { text: string; tone?: "you" | "bad" | "busy" }[] {
  if (j.status === "queued") return [{ text: "Queued", tone: "busy" }];
  if (["running", "applying"].includes(j.status)) return [{ text: "Running", tone: "busy" }];
  if (j.status === "failed") return [{ text: "Stopped", tone: "bad" }];
  if (j.status === "cancelled") return [{ text: "PR closed" }];
  const s = j.summary ?? {};
  const n = s.hunks ?? 0;
  if (!n) return [{ text: "Clean merge" }];
  const review = s.needs_review ?? 0;
  const chips: { text: string; tone?: "you" | "bad" | "busy" }[] = [{ text: `${n} ${n === 1 ? "conflict" : "conflicts"}` }, { text: `${s.auto_applied ?? 0} auto` }];
  if (review && s.pr !== "merged") chips.push({ text: `${review} ${j.kind === "preview" ? "would need you" : "need you"}`, tone: "you" });
  else if (j.kind === "sync" && s.ci === "failure") chips.push({ text: "CI failed", tone: "bad" });
  else if (j.kind === "sync") chips.push({ text: s.pr === "merged" ? "Merged" : "Ready to merge" });
  return chips;
}

function mark(j: JobSummary) {
  if (j.status === "failed") return "error" as const;
  if (isActive(j.status)) return "needs" as const;
  if ((j.summary?.needs_review ?? 0) > 0 && j.summary?.pr !== "merged") return "needs" as const;
  if (j.summary?.ci === "failure") return "needs" as const;
  return "auto" as const;
}

/** Every sync and preview in the workspace, newest first. */
export default function Activity() {
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);
  const [error, setError] = useState<{ message: string; status?: number } | null>(null);

  const [me] = useMe();
  useEffect(() => {
    if (!me) return;
    let stop = false;
    const load = () =>
      listJobs()
        .then((j) => !stop && (setJobs(j), setError(null)))
        .catch((e: Error & { status?: number }) => !stop && setError({ message: e.message, status: e.status }));
    load();
    const t = setInterval(load, 5000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [me]);

  if (me === null || error?.status === 401)
    return (
      <div className="wrap pb-16 pt-10">
        <h1 className="t-h1">Activity</h1>
        <p className="mt-2 text-muted">Sign in to see your syncs, or <Link href="/demo" className="link">look through a recorded one</Link>.</p>
        <Link href="/signin?next=/monitor" className="btn btn-accent mt-6">Sign in with GitHub</Link>
      </div>
    );

  return (
    <div className="wrap max-w-[880px] pb-16 pt-10">
      <h1 className="t-h1">Activity</h1>
      <p className="mt-2 text-muted">Every sync and preview, newest first.</p>
      {error && <p role="alert" className="mt-4 text-danger">{error.message}</p>}
      {jobs === null && !error && <p className="mt-8 text-muted">Loading…</p>}
      {jobs?.length === 0 && (
        <p className="mt-8 border-t border-line pt-6 text-muted">
          Nothing has run yet. <Link href="/repos" className="link">Start a preview from your repositories</Link>.
        </p>
      )}
      {jobs && jobs.length > 0 && (
        <div className="mt-6 space-y-6">
          {Object.entries(
            jobs.reduce<Record<string, JobSummary[]>>((g, j) => ((g[dayLabel(j.created_at)] ??= []).push(j), g), {}),
          ).map(([day, list]) => (
            <section key={day} aria-label={day}>
              <h2 className="t-label text-muted">{day}</h2>
              <ul className="sheet mt-2 divide-y divide-line overflow-hidden">
                {list.map((j) => (
                  <li key={j.id}>
                    <Link href={`/jobs/${j.id}`} title={headline(j)} className="activity-row grid grid-cols-[14px_1fr_auto] items-center gap-x-3 gap-y-1 px-4 py-3 sm:grid-cols-[14px_minmax(0,1fr)_auto_64px]">
                      <HunkMark state={mark(j)} />
                      <span className="flex min-w-0 items-center gap-2">
                        <span className="truncate font-mono text-[14px]">{j.repo}</span>
                        <span className="kind-pill">{j.kind === "preview" ? "Preview" : "Sync"}</span>
                      </span>
                      <span className="col-span-2 col-start-2 flex flex-wrap gap-1 sm:col-span-1 sm:col-start-3">
                        {outcome(j).map((c) => (
                          <span key={c.text} className={`chip-out ${c.tone ? `chip-out-${c.tone}` : ""}`}>{c.text}</span>
                        ))}
                      </span>
                      <time className="row-start-1 col-start-3 text-right t-label tabular-nums text-faint sm:col-start-4">{time(j.created_at)}</time>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
