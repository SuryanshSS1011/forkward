import { Sym } from "@/components/Sym";

export default function Loading() {
  return (
    <div className="parchment flex min-h-screen flex-col items-center justify-center gap-4" role="status" aria-live="polite">
      <span className="flex h-16 w-16 animate-pulse items-center justify-center rounded-full border-2 border-[var(--pa-outline)] bg-[var(--pa-primary-fixed)] shadow-[3px_3px_0px_#8a716e]">
        <Sym name="restaurant" className="text-[30px] text-[var(--pa-primary)]" />
      </span>
      <p className="font-label text-[13px] uppercase tracking-widest text-[var(--pa-ink-muted)]">Loading Forkward…</p>
    </div>
  );
}
