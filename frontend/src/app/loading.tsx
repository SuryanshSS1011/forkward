import { AgentMark } from "@/components/AgentMark";

export default function Loading() {
  return (
    <div className="wrap flex min-h-[50vh] flex-col items-center justify-center gap-4 py-12" role="status" aria-live="polite">
      <AgentMark state="working" size={88} title="Loading" />
      <p className="t-label text-muted">Loading Forkward…</p>
    </div>
  );
}
