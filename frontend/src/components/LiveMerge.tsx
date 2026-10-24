"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { Scene } from "@/lib/demo/scenes";
import { AgentGlyph, AgentMark, RuleGlyph, type AgentState } from "./AgentMark";

// One hunk's timeline, in milliseconds: read, three agents vote, three gate checks, the decision.
const VOTE_AT = [900, 1400, 1900];
const CHECK_AT = [2500, 2900, 3300];
const CONF_AT = 3700;
const DECIDE_AT = 4300;
const END = 7200;
const TICK = 100;

function useReducedMotion() {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const q = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(q.matches);
    const on = () => setReduced(q.matches);
    q.addEventListener("change", on);
    return () => q.removeEventListener("change", on);
  }, []);
  return reduced;
}

function Code({ label, tone, lines }: { label: string; tone: "fork" | "up"; lines: string[] }) {
  return (
    <figure className={`lm-pane lm-pane-${tone} min-w-0`}>
      <figcaption className="t-label text-muted">{label}</figcaption>
      <pre className="lm-code">{lines.length ? lines.join("\n") : "(nothing)"}</pre>
    </figure>
  );
}

/**
 * The home page hero: the merge agent replays three hunks from a real recorded sync, one at a time.
 * It plays through once and stops on the last hunk. Hover or focus holds it, and picking a hunk
 * replays just that one and stops the autoplay. Under reduced motion it shows each outcome at once.
 */
export function LiveMerge({ scenes }: { scenes: Scene[] }) {
  const reduced = useReducedMotion();
  const [i, setI] = useState(0);
  const [t, setT] = useState(0);
  const [held, setHeld] = useState(false);
  const [auto, setAuto] = useState(true);
  const root = useRef<HTMLDivElement>(null);
  const running = !reduced && !held && t < END;

  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setT((x) => x + TICK), TICK);
    return () => clearInterval(id);
  }, [running]);
  useEffect(() => {
    if (t < END || !auto || i >= scenes.length - 1) return;
    setI(i + 1);
    setT(0);
  }, [t, auto, i, scenes.length]);

  const s = scenes[i];
  if (!s) return null;
  const now = reduced ? END : t;
  const voted = VOTE_AT.filter((at) => now >= at).length;
  const checked = CHECK_AT.filter((at) => now >= at).length;
  const decided = now >= DECIDE_AT;
  const state: AgentState = decided ? (s.applied ? "verified" : "waiting") : "working";
  const [file, n] = s.ref.split("#");
  const parts = file.split("/");
  const dir = parts.length > 2 ? `…/${parts.at(-2)}/` : parts.slice(0, -1).map((x) => `${x}/`).join("");
  const status = decided
    ? s.applied
      ? "Applied in this recording."
      : "Sent to you to review."
    : now >= CHECK_AT[0]
      ? "Checking the gate."
      : now >= VOTE_AT[0]
        ? `${s.agents.length} agents are proposing resolutions.`
        : "Reading the conflict.";

  const go = (k: number) => {
    setAuto(false);
    setI(k);
    setT(reduced ? END : 0);
  };

  return (
    <div
      ref={root}
      className="lm card min-w-0 overflow-hidden rounded-card shadow-card"
      onMouseEnter={() => setHeld(true)}
      onMouseLeave={() => setHeld(false)}
      onFocus={() => setHeld(true)}
      onBlur={(e) => {
        if (!root.current?.contains(e.relatedTarget as Node | null)) setHeld(false);
      }}
      role="region"
      aria-label="A recorded Forkward sync, replayed"
    >
      <div className="flex items-center gap-4 border-b border-line px-4 py-3">
        <AgentMark state={state} size={88} />
        <div className="min-w-0 flex-1">
          <p className="font-mono text-[12px] leading-[18px] text-faint">Recorded sync · hunk {i + 1} of {scenes.length}</p>
          <p className="text-[16px] font-semibold leading-[24px]" aria-live="polite">{status}</p>
          <p className="truncate font-mono text-[12px] leading-[18px] text-muted" title={s.ref}>
            <span className="text-faint">{dir}</span>{parts.at(-1)}<span className="text-faint">#{n}</span>
          </p>
        </div>
      </div>

      <div className="lm-tabs" role="group" aria-label="Choose a hunk">
        {scenes.map((x, k) => (
          <button key={x.ref} type="button" onClick={() => go(k)} aria-pressed={k === i} className="lm-tab">
            <span aria-hidden className={`lm-tab-dot ${x.applied ? "lm-tab-auto" : "lm-tab-you"}`} />
            {x.tag}
            <span aria-hidden className="lm-tab-bar" style={{ transform: `scaleX(${k === i ? Math.min(1, now / END) : k < i && auto ? 1 : 0})` }} />
          </button>
        ))}
      </div>

      <div key={s.ref} className="fade-swap grid grid-cols-1 gap-4 px-4 py-4">
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <Code label="Your fork" tone="fork" lines={s.fork} />
          <Code label="Upstream" tone="up" lines={s.upstream} />
        </div>

        <ul className="grid grid-cols-1 gap-2" aria-label="Proposals">
          {s.options.map((o, k) => {
            const votes = s.agents.slice(0, voted).filter((a) => a.option === k).length;
            const win = decided && o.chosen;
            return (
              <li key={k} className={`lm-option ${win ? "lm-option-win" : ""}`}>
                <div className="flex items-center justify-between gap-3">
                  <span className="min-w-0 truncate text-[14px] font-medium">{o.title}</span>
                  <span className="flex shrink-0 items-center gap-1">
                    {o.rules.slice(0, 2).map((r) => (
                      <span key={r} title={r} className={now >= VOTE_AT[0] ? "lm-vote" : "lm-pending"}><RuleGlyph size={16} /></span>
                    ))}
                    {s.agents.map((a, j) =>
                      a.option === k ? (
                        <span key={a.name} title={a.name} className={j < voted ? "lm-vote" : "lm-pending"}><AgentGlyph size={20} /></span>
                      ) : null,
                    )}
                    <span className="ml-1 w-[3ch] text-right font-mono text-[12px] tabular-nums text-muted">{votes}/{s.agents.length}</span>
                  </span>
                </div>
                <span className="lm-bar" aria-hidden><span style={{ transform: `scaleX(${votes / Math.max(1, s.agents.length)})` }} /></span>
              </li>
            );
          })}
        </ul>

        <ul className="grid gap-1 text-[14px]" aria-label="Gate checks">
          {s.checks.map((c, k) => {
            const shown = k < checked;
            return (
              <li key={c.text} className={`flex items-center gap-2 transition-opacity duration-300 ${shown ? "opacity-100" : "opacity-30"}`}>
                <span aria-hidden className={`lm-check ${shown ? (c.ok ? "lm-check-ok" : "lm-check-no") : ""}`} />
                <span>{c.text}</span>
                {shown && <span className="sr-only">{c.ok ? ", passed" : ", failed"}</span>}
              </li>
            );
          })}
          <li className={`flex items-center gap-2 transition-opacity duration-300 ${now >= CONF_AT ? "opacity-100" : "opacity-30"}`}>
            <span className="lm-meter" aria-hidden><span style={{ transform: `scaleX(${now >= CONF_AT ? s.confidence : 0})` }} /></span>
            <span>Confidence <span className="font-mono tabular-nums">{s.confidence.toFixed(2)}</span></span>
          </li>
        </ul>

        <p className={`lm-verdict ${decided ? (s.applied ? "lm-verdict-auto" : "lm-verdict-you") : ""}`}>
          {decided ? s.reason : "\u00a0"}
          {decided && s.applied && <span className="mt-1 block text-muted">Live syncs send it to you until the gate certifies its error bound.</span>}
        </p>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-t border-line px-4 py-2">
        <span className="t-label text-faint">Recorded under an earlier gate</span>
        <Link href="/demo/live" className="link text-[14px]">Open the whole run</Link>
      </div>
    </div>
  );
}
