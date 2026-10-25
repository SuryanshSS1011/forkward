import type { JobSummary, Stage } from "@/lib/jobs";
import { PREVIEW_STAGES, STAGES } from "./words";

/** The run's stages in order, with the current one marked. These are a real sequence, so they are
 * numbered by position rather than decorated. */
export function StageTrack({ job, hide = [] }: { job: JobSummary; hide?: Stage[] }) {
  const steps = (job.kind === "preview" ? PREVIEW_STAGES : STAGES).filter((s) => !hide.includes(s.key));
  const current = job.stage ?? (job.status === "queued" ? null : "fetch");
  const at = current ? steps.findIndex((s) => s.key === current) : -1;
  const finished = job.status === "succeeded" || job.status === "cancelled";
  const failed = job.status === "failed";
  return (
    <ol className="stage-track" aria-label="Progress">
      {steps.map((s, i) => {
        const done = finished || i < at;
        const now = !finished && i === at;
        const state = failed && now ? "failed" : done ? "done" : now ? "now" : "todo";
        const progress = s.key === "resolve" && now && job.progress?.total ? ` ${job.progress.done ?? 0}/${job.progress.total}` : "";
        return (
          <li key={s.key} className={`stage stage-${state}`} aria-current={now ? "step" : undefined}>
            <span className="stage-mark" aria-hidden />
            <span className="stage-label">
              {s.label}
              {progress && <span className="tabular-nums text-muted">{progress}</span>}
            </span>
            <span className="sr-only">{state === "done" ? ", done" : state === "now" ? ", in progress" : state === "failed" ? ", stopped here" : ""}</span>
          </li>
        );
      })}
    </ol>
  );
}
