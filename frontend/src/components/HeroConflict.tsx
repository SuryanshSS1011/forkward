"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { dedent, sourceName } from "@/components/job/words";
import { Sym } from "@/components/Sym";
import type { HunkRow } from "@/lib/jobs";

const RESOLUTION: Record<string, string> = { keep_vendor: "Keep your fork", take_upstream: "Take upstream", merge: "Keep both" };
// Played in this order: each kind of resolution once, ending on the one only the models backed.
const ORDER = ["take_upstream", "keep_vendor", "merge"];

const chosenOf = (h: HunkRow) => h.detail?.candidates?.find((c) => c.chosen);
const rank = (h: HunkRow) => {
  const c = chosenOf(h);
  const ruled = (c?.sources ?? []).some((s) => !s.startsWith("model:"));
  return (ruled ? 0 : 10) + Math.max(0, ORDER.indexOf(c?.label ?? ""));
};

/**
 * The conflicts of one recorded run, replayed one file at a time in the order each was decided:
 * both sides, the model votes, the rule that agreed, the resolution written in, then the gate's
 * verdict. Tabs jump between files. The server renders the first file finished, so the card reads
 * without JavaScript and stays still under reduced motion.
 */
export function HeroConflict({ hunks }: { hunks: HunkRow[] }) {
  const files = [...hunks].sort((a, b) => rank(a) - rank(b));
  const [idx, setIdx] = useState(0);
  const hunk = files[idx];
  const chosen = chosenOf(hunk);
  const models = (chosen?.sources ?? []).filter((s) => s.startsWith("model:"));
  const rule = (chosen?.sources ?? []).find((s) => !s.startsWith("model:"));
  const lines = dedent((hunk.resolution ?? "").split("\n"));
  const voters = hunk.detail?.proposers ?? models.length;
  // Steps: 0 sides, 1..voters votes, then the rule, then each resolution line, then the verdict.
  const RULE = voters + 1;
  const FIRST_LINE = RULE + 1;
  const DONE = FIRST_LINE + lines.length;
  const [step, setStep] = useState(DONE);
  const [playing, setPlaying] = useState(false);
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const io = new IntersectionObserver(([e]) => setPlaying(e.isIntersecting));
    if (ref.current) io.observe(ref.current);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!playing) {
      setStep(DONE);
      return;
    }
    let s = 0;
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => {
      setStep(s);
      if (s === DONE) {
        timer = setTimeout(() => setIdx((i) => (i + 1) % files.length), 3200);
        return;
      }
      timer = setTimeout(tick, s >= FIRST_LINE ? 320 : 520);
      s += 1;
    };
    tick();
    return () => clearTimeout(timer);
  }, [playing, idx, DONE, FIRST_LINE, files.length]);

  const done = step >= DONE;
  const shown = Math.max(0, Math.min(lines.length, step - FIRST_LINE + 1));
  const verdict = hunk.detail?.passed_checks
    ? "Every check agreed. It waits for you until the gate is certified for this model."
    : "Only the models proposed this and no rule backs it, so it waits for you.";
  return (
    <figure
      ref={ref}
      aria-label={`Conflicts from a recorded run. Showing ${hunk.file}: ${RESOLUTION[chosen?.label ?? ""] ?? "resolved"}, waiting for a person.`}
      className="pa-lift relative z-10 w-full max-w-md border-2 border-[var(--pa-outline)] bg-[var(--pa-surface)] p-5 shadow-[5px_5px_0px_#8a716e]"
      style={{ borderRadius: "12px 5px 16px 8px / 6px 15px 7px 14px" }}
    >
      <div role="tablist" aria-label="Files in this run" className="-mx-1 mb-3 flex gap-1 overflow-x-auto pb-1">
        {files.map((f, i) => (
          <button
            key={f.id}
            role="tab"
            aria-selected={i === idx}
            onClick={() => setIdx(i)}
            className={`hc-tab shrink-0 px-2 py-1 font-mono text-[11px] ${i === idx ? "hc-tab-on" : ""}`}
          >
            {f.file.split("/").pop()}
          </button>
        ))}
      </div>

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

      <div className="flex min-h-[20px] flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-[var(--pa-ink-muted)]">
        <span className="flex items-center gap-1" aria-hidden>
          {Array.from({ length: voters }, (_, i) => (
            <span key={i} className={`hc-vote ${step >= i + 1 && i < models.length ? "hc-vote-on" : ""}`} />
          ))}
        </span>
        <span className="font-label">
          {Math.min(models.length, Math.max(0, step))} of {voters} models agree
        </span>
        <span className={`hc-rule font-label ${step >= RULE ? "hc-rule-on" : ""}`}>{rule ? `+ ${sourceName(rule).toLowerCase()}` : "no rule agrees"}</span>
      </div>

      <div className="mt-2 rounded-sm border-l-2 border-[var(--pa-tertiary)] bg-[var(--pa-surface-low)] p-2 text-[11px]">
        <span className="mb-1 block font-label text-[var(--pa-tertiary)]">Proposed: {(RESOLUTION[chosen?.label ?? ""] ?? "a resolution").toLowerCase()}</span>
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
        className={`hc-verdict mt-3 min-h-[66px] rounded-md border-2 border-[var(--pa-outline-variant)] bg-[var(--pa-surface-high)] p-3 text-[14px] leading-snug text-[var(--pa-ink)] ${done ? "hc-verdict-on" : ""}`}
      >
        <span className="font-bold">Confidence {Math.round((hunk.confidence ?? 0) * 100)}%.</span> {verdict}
      </figcaption>

      <div className="mt-3 flex items-center justify-between font-label text-[11px] text-[var(--pa-ink-muted)]">
        <span>
          {idx + 1} of {files.length} conflicts in a recorded preview
        </span>
        <Link href="/demo" className="text-[var(--pa-primary)] underline hover:text-[var(--pa-ink)]">
          See the full run
        </Link>
      </div>
    </figure>
  );
}
