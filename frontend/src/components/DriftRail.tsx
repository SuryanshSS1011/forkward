import type { JobSummary } from "@/lib/jobs";

/**
 * A fork and its upstream drawn as two lines from a shared base, with a merge stroke bringing
 * upstream into the fork. The last run's conflicts sit on that stroke as marks: applied, waiting
 * for a person, or failed. With no upstream set, the upstream line is dashed and has no merge.
 */
export function DriftRail({ upstream, latest }: { upstream: string | null; latest: JobSummary | null }) {
  const s = latest?.summary ?? {};
  const total = s.hunks ?? 0;
  const auto = Math.min(s.auto_applied ?? 0, total);
  const review = Math.min(s.needs_review ?? 0, total - auto);
  const marks = [
    ...Array<"auto">(auto).fill("auto"),
    ...Array<"review">(review).fill("review"),
    ...Array<"error">(Math.max(0, total - auto - review)).fill("error"),
  ].slice(0, 12);
  const merged = !!upstream && !!latest && latest.status !== "failed";
  const label = !upstream
    ? "No upstream yet."
    : !latest
      ? `Follows ${upstream}. Nothing has run yet.`
      : total === 0
        ? `Follows ${upstream}. The last run merged cleanly.`
        : `Follows ${upstream}. Last run: ${total} conflicts, ${auto} applied, ${review} waiting for you.`;

  // Marks sit on the merge stroke (a cubic from (300,14) to (540,46)), spread between its ends.
  const at = (i: number) => {
    const t = marks.length === 1 ? 0.5 : 0.15 + (0.7 * i) / (marks.length - 1);
    const u = 1 - t;
    const bez = (a: number, b: number, c: number, d: number) => u * u * u * a + 3 * u * u * t * b + 3 * u * t * t * c + t * t * t * d;
    return { cx: bez(300, 360, 460, 540), cy: bez(14, 14, 46, 46) };
  };

  return (
    <svg viewBox="0 0 640 60" className="drift-rail" role="img" aria-label={label} preserveAspectRatio="none">
      <path d="M8 46 C 40 46, 56 14, 96 14 L 632 14" className={`rail-up ${upstream ? "" : "rail-missing"}`} />
      <path d="M8 46 L 632 46" className="rail-fork" />
      <circle cx="8" cy="46" r="4" className="rail-base" />
      {merged && <path d="M300 14 C 360 14, 460 46, 540 46" className="rail-merge" />}
      {merged && marks.map((m, i) => <circle key={i} {...at(i)} r="5" className={`rail-mark rail-${m}`} />)}
      {merged && <circle cx="540" cy="46" r="5" className="rail-land" />}
    </svg>
  );
}
