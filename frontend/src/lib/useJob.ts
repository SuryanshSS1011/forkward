"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { getJob } from "@/lib/api/jobs";
import { isActive, type JobDetail } from "@/lib/jobs";

/**
 * A job, kept current by polling /api/jobs/[id]. Polls every 1.5 s while the worker is busy,
 * every 10 s while the job waits on CI or a reviewer, and stops once it has finished. Events are
 * fetched incrementally with ?after=, so a long job never resends its history.
 */
export function useJob(id: string) {
  const [job, setJob] = useState<JobDetail | null>(null);
  const [error, setError] = useState<{ message: string; status?: number } | null>(null);
  const last = useRef(0);
  const events = useRef<JobDetail["events"]>([]);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    try {
      const j = await getJob(id, last.current);
      if (j.events.length) {
        // Two loads can overlap (a refresh during a poll), so events merge by id, never append.
        const seen = new Set(events.current.map((e) => e.id));
        events.current = [...events.current, ...j.events.filter((e) => !seen.has(e.id))].sort((a, b) => a.id - b.id);
        last.current = Math.max(last.current, events.current[events.current.length - 1].id);
      }
      setJob({ ...j, events: events.current });
      setError(null);
      return j;
    } catch (e) {
      const err = e as Error & { status?: number };
      setError({ message: err.message, status: err.status });
      return null;
    }
  }, [id]);

  const tickRef = useRef<() => Promise<void>>(async () => undefined);

  useEffect(() => {
    let stopped = false;
    const tick = async () => {
      if (timer.current) clearTimeout(timer.current);
      const j = await load();
      if (stopped) return;
      const waiting = j && ["waiting_ci", "waiting_approval"].includes(j.status);
      const busy = j && (isActive(j.status) || j.review_requested > j.review_applied);
      if (!j || busy || waiting) timer.current = setTimeout(tick, busy ? 1500 : j ? 10_000 : 5000);
    };
    tickRef.current = tick;
    tick();
    return () => {
      stopped = true;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [load]);

  /** Re-fetches now and resumes polling at the right pace, for example right after a review. */
  const refresh = useCallback(() => tickRef.current(), []);

  return { job, error, refresh, setJob };
}
