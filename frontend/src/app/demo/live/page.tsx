"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { JobView } from "@/components/job/JobView";
import { SAMPLE_JOB } from "@/lib/demo/sample";
import type { HunkRow, JobDetail, Stage } from "@/lib/jobs";

type Replay = { stage: Stage; done: number } | null;

/** The replay's beats: each stage holds for a moment, and the resolve stage counts up hunk by hunk. */
const BEATS: { stage: Stage; ms: number; done?: number }[] = [
  { stage: "fetch", ms: 700 },
  { stage: "merge", ms: 900 },
  ...SAMPLE_JOB.hunks.map((_, i) => ({ stage: "resolve" as Stage, ms: 650, done: i + 1 })),
  { stage: "review", ms: 0 },
];

export default function SampleRun() {
  const [hunks, setHunks] = useState<HunkRow[]>(SAMPLE_JOB.hunks);
  const [committed, setCommitted] = useState(0);
  const [replay, setReplay] = useState<Replay>(null);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  const play = () => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    setHunks(SAMPLE_JOB.hunks);
    setCommitted(0);
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return setReplay(null);
    let t = 0;
    for (const b of BEATS) {
      timers.current.push(setTimeout(() => setReplay(b.stage === "review" ? null : { stage: b.stage, done: b.done ?? (b.stage === "resolve" ? 0 : BEATS.length) }), t));
      t += b.ms;
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  const job: JobDetail = useMemo(() => {
    const needs = hunks.filter((h) => h.decision === "review" && !h.review_commit).length;
    const base: JobDetail = {
      ...SAMPLE_JOB,
      hunks,
      review_requested: committed,
      review_applied: committed,
      summary: { ...SAMPLE_JOB.summary, needs_review: needs },
    };
    if (!replay) return base;
    const resolving = replay.stage === "resolve";
    const shown = resolving ? replay.done : ["fetch", "merge"].includes(replay.stage) ? 0 : hunks.length;
    return {
      ...base,
      status: "running",
      stage: replay.stage,
      progress: { done: shown, total: hunks.length },
      hunks: hunks.slice(0, shown),
      events: [],
    };
  }, [hunks, committed, replay]);

  return (
    <JobView
      job={job}
      stage={replay?.stage}
      hideStages={["open_pr", "ci"]}
      applyLabel={(() => {
        const n = hunks.filter((h) => h.review === "accepted" && !h.review_commit).length;
        return `Commit ${n} accepted ${n === 1 ? "hunk" : "hunks"} (sample, nothing is pushed)`;
      })()}
      onReview={async (h, decision, resolution) => {
        setHunks((all) => all.map((x) => (x.id === h.id ? { ...x, review: decision, final_resolution: decision === "accepted" ? (resolution ?? null) : null } : x)));
      }}
      onApply={async () => {
        setHunks((all) => all.map((x) => (x.review === "accepted" && !x.review_commit ? { ...x, review_commit: "sample", applied: true } : x)));
        setCommitted((n) => n + 1);
      }}
      extraActions={
        <button className="btn btn-secondary" onClick={play} disabled={!!replay}>
          {replay ? "Replaying…" : "Replay the run"}
        </button>
      }
      note={
        <span className="flex items-baseline gap-2">
          <span className="kind-pill">Recorded run</span>
          <span>Real conflicts and votes, decided under an earlier gate that could auto-apply. Live syncs send every hunk to you until the gate certifies its error bound. Nothing here is pushed.</span>
        </span>
      }
    />
  );
}
