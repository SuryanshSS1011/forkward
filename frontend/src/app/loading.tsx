import { AgentGlyph } from "@/components/AgentMark";

/** Shown while any route streams in, so it stays neutral: the app's own tokens, not the parchment
 * skin of the marketing pages. */
export default function Loading() {
  return (
    <div className="flex min-h-[50vh] flex-col items-center justify-center gap-3 text-muted" role="status" aria-live="polite">
      <span className="animate-pulse motion-reduce:animate-none">
        <AgentGlyph size={40} />
      </span>
      <p className="text-[14px]">Loading…</p>
    </div>
  );
}
