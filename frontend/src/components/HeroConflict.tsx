"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { dedent, sourceName } from "@/components/job/words";
import { Sym } from "@/components/Sym";
import type { HunkRow } from "@/lib/jobs";

/**
 * One recorded conflict replayed in the order it was decided: both sides, the three model votes,
 * the rule that agreed, the resolution written in, then the gate's verdict. The server renders the
 * finished card, so it reads without JavaScript and stays still under reduced motion.
 */
export function HeroConflict({ hunk, total }: { hunk: HunkRow; total: number }) {
  const chosen = hunk.detail?.candidates?.find((c) => c.chosen);
  const models = (chosen?.sources ?? []).filter((s) => s.startsWith("model:"));
  const rule = (chosen?.sources ?? []).find((s) => !s.startsWith("model:"));
  const lines = dedent((hunk.resolution ?? "").split("\n"));
  const voters = hunk.detail?.proposers ?? models.length;
  // Steps: 0 sides, 1..voters votes, then the rule, then each resolution line, then the verdict.
  const RULE = voters + 1;
  const FIRST_LINE = RULE + 1;
  const DONE = FIRST_LINE + lines.length;
  const [step, setStep] = useState(DONE);
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let visible = false;
    const run = (s: number) => {
      setStep(s);
      if (!visible) return;
      const wait = s === DONE ? 4200 : s >= FIRST_LINE ? 380 : 650;
      timer = setTimeout(() => run(s === DONE ? 0 : s + 1), wait);
    };
    const io = new IntersectionObserver(([e]) => {
      visible = e.isIntersecting;
      clearTimeout(timer);
      if (visible) run(0);
      else setStep(DONE);
    });
    if (ref.current) io.observe(ref.current);
    return () => {
      io.disconnect();
      clearTimeout(timer);
    };
  }, [DONE, FIRST_LINE]);

  const done = step >= DONE;
  const shown = Math.max(0, Math.min(lines.length, step - FIRST_LINE + 1));
  return (
    <figure
      ref={ref}
      aria-label={`A recorded conflict in ${hunk.file}: ${models.length} models and a rule agreed on keeping both sides, and it waits for a person.`}
      className="pa-lift relative z-10 w-full max-w-md border-2 border-[var(--pa-outline)] bg-[var(--pa-surface)] p-5 shadow-[5px_5px_0px_#8a716e]"
      style={{ borderRadius: "12px 5px 16px 8px / 6px 15px 7px 14px" }}
    >
      <div className="flex items-center justify-between gap-3 border-b border-dashed border-[var(--pa-outline-variant)] pb-3">
        <div className="flex min-w-0 items-center gap-2">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-[var(--pa-outline)] bg-[var(--pa-primary-fixed)]">
            <Sym name="merge" className="text-[22px] text-[var(--pa-primary)]" />
          </div>
          <div className="min-w-0">
            <p className="font-label text-[11px] uppercase tracking-wider text-[var(--pa-ink-muted)]">Conflict</p>
            <p className="truncate font-mono text-[13px] font-bold text-[var(--pa-ink)]">{hunk.file}</p>
          </div>
        </div>
        <span
          className={`hc-badge shrink-0 border px-2 py-0.5 font-label text-[11px] ${done ? "hc-badge-done" : ""}`}
          style={{ borderRadius: "4px 7px 3px 6px" }}
          aria-live="polite"
        >
          {done ? "Waiting for you" : "Resolving"}
        </span>
      </div>

      <div className="my-3 grid grid-cols-2 gap-2 text-[11px]">
        <div className="min-w-0 rounded-sm border-l-2 border-[var(--pa-secondary)] bg-[var(--pa-surface-low)] p-2">
          <span className="mb-1 block font-label text-[var(--pa-secondary)]">Your fork</span>
          <pre className="overflow-x-auto text-[10px] leading-tight text-[var(--pa-ink)]">
            <code>{dedent(hunk.detail?.vendor ?? []).join("\n")}</code>
          </pre>
        </div>
        <div className="min-w-0 rounded-sm border-l-2 border-[var(--pa-primary)] bg-[var(--pa-surface-low)] p-2">
          <span className="mb-1 block font-label text-[var(--pa-primary)]">Upstream</span>
          <pre className="overflow-x-auto text-[10px] leading-tight text-[var(--pa-ink)]">
            <code>{dedent(hunk.detail?.upstream ?? []).join("\n")}</code>
          </pre>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-[var(--pa-ink-muted)]">
        <span className="flex items-center gap-1" aria-hidden>
          {Array.from({ length: voters }, (_, i) => (
            <span key={i} className={`hc-vote ${step >= i + 1 && i < models.length ? "hc-vote-on" : ""}`} />
          ))}
        </span>
        <span className="font-label">
          {Math.min(models.length, Math.max(0, step))} of {voters} models agree
        </span>
        {rule && <span className={`hc-rule font-label ${step >= RULE ? "hc-rule-on" : ""}`}>+ {sourceName(rule).toLowerCase()}</span>}
      </div>

      <div className="mt-2 rounded-sm border-l-2 border-[var(--pa-tertiary)] bg-[var(--pa-surface-low)] p-2 text-[11px]">
        <span className="mb-1 block font-label text-[var(--pa-tertiary)]">Proposed: keep both</span>
        <pre className="min-h-[52px] overflow-x-auto text-[10px] leading-tight text-[var(--pa-ink)]">
          <code>
            {lines.map((l, i) => (
              <span key={i} className={`hc-line ${i < shown ? "hc-line-on" : ""}`}>
                {l}
                {"\n"}
              </span>
            ))}
          </code>
        </pre>
      </div>

      <figcaption
        className={`hc-verdict mt-3 rounded-md border-2 border-[var(--pa-outline-variant)] bg-[var(--pa-surface-high)] p-3 text-[14px] leading-snug text-[var(--pa-ink)] ${done ? "hc-verdict-on" : ""}`}
      >
        <span className="font-bold">Confidence {Math.round((hunk.confidence ?? 0) * 100)}%.</span> It waits for you until the gate is certified for this model.
      </figcaption>

      <div className="mt-3 flex items-center justify-between font-label text-[11px] text-[var(--pa-ink-muted)]">
        <span>1 of {total} conflicts in a recorded run</span>
        <Link href="/demo" className="text-[var(--pa-primary)] underline hover:text-[var(--pa-ink)]">
          See the full run
        </Link>
      </div>
    </figure>
  );
}
