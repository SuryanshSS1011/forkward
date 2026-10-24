"use client";
import { useEffect, useRef, useState } from "react";
import { KEEP_FORK, ROUNDS, TRAIN_N, VALIDATION_N, parseDecision } from "@/lib/results";

const H = 244;
const PAD = { l: 44, r: 20, t: 14, b: 44 };
const LO = 0.5;
const HI = 0.85;
const y = (p: number) => PAD.t + ((HI - p) / (HI - LO)) * (H - PAD.t - PAD.b);
const pct = (p: number) => `${(p * 100).toFixed(1)}%`;
const pts = (p: number) => `${p >= 0 ? "+" : "−"}${Math.abs(p * 100).toFixed(1)} pts`;

/** What the promotion gate decided in a round, as a verdict, a few figures and one sentence. */
function readout(i: number) {
  const r = ROUNDS[i];
  const d = parseDecision(r.decision);
  const val = `${Math.round(r.validation * VALIDATION_N)}/${VALIDATION_N}`;
  if (!d) return { verdict: "Starting point", tone: "start", stats: [["Validation", val], ["Train", pct(r.train)]], line: "The strategy every child has to beat." };
  const stats = [["Validation", val], ["Gain", pts(d.delta)], ["Noise", `\u00b1${pts(d.std).slice(1)}`]];
  if (r.promoted) return { verdict: "Promoted", tone: "up", stats, line: `The 95% interval, ${pts(d.lo)} to ${pts(d.hi)}, still includes zero.` };
  return { verdict: "Kept incumbent", tone: "kept", stats, line: "The best child did not clear the noise." };
}

/** The strategy search, round by round: the best strategy on train, the incumbent on validation,
 * and the keep-the-fork baseline. Hover or pick a round to read the promotion decision. */
export function LearningCurve() {
  const [active, setActive] = useState(ROUNDS.length - 1);
  const [drawn, setDrawn] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const plot = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || !("IntersectionObserver" in window)) return setDrawn(true);
    const io = new IntersectionObserver(([e]) => e.isIntersecting && (setDrawn(true), io.disconnect()), { threshold: 0.4 });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  // The chart is drawn at its container's width, so its text stays 12px on a phone.
  const [W, setW] = useState(560);
  useEffect(() => {
    const el = plot.current;
    if (!el || !("ResizeObserver" in window)) return;
    const ro = new ResizeObserver(([e]) => setW(Math.round(Math.max(300, Math.min(640, e.contentRect.width)))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const x = (k: number) => PAD.l + (k * (W - PAD.l - PAD.r)) / (ROUNDS.length - 1);
  const line = (key: "train" | "validation") => ROUNDS.map((d, i) => `${i ? "L" : "M"}${x(d.round)} ${y(d[key])}`).join(" ");
  // The incumbent only changes at a promotion, so its line is a step.
  const steps = ROUNDS.map((d, i) => (i ? `H${x(d.round)} V${y(d.validation)}` : `M${x(d.round)} ${y(d.validation)}`)).join(" ");
  const r = ROUNDS[active];
  const o = readout(active);

  return (
    <div ref={ref} className={`lc grid min-w-0 gap-6 lg:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)] lg:items-center lg:gap-10 ${drawn ? "lc-drawn" : ""}`}>
      <div ref={plot} className="min-w-0">
        <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full" role="img" aria-labelledby="lc-title">
          <title id="lc-title">{`Validation accuracy of the incumbent rose from ${pct(ROUNDS[0].validation)} to ${pct(ROUNDS.at(-1)!.validation)} over ${ROUNDS.length - 1} rounds, against ${pct(KEEP_FORK.validation)} for always keeping the fork.`}</title>
          {[0.5, 0.6, 0.7, 0.8].map((g) => (
            <g key={g}>
              <line x1={PAD.l} x2={W - PAD.r} y1={y(g)} y2={y(g)} className="lc-grid" />
              <text x={PAD.l - 8} y={y(g) + 4} textAnchor="end" className="lc-axis">{g * 100}%</text>
            </g>
          ))}
          {ROUNDS.map((d) => (
            <text key={d.round} x={x(d.round)} y={H - 24} textAnchor="middle" className={`lc-axis ${d.round === active ? "lc-axis-on" : ""}`}>{d.round}</text>
          ))}
          <text x={(PAD.l + W - PAD.r) / 2} y={H - 4} textAnchor="middle" className="lc-axis">Round</text>
          <line x1={x(active)} x2={x(active)} y1={PAD.t} y2={H - PAD.b} className="lc-guide" />

          <line x1={PAD.l} x2={W - PAD.r} y1={y(KEEP_FORK.validation)} y2={y(KEEP_FORK.validation)} className="lc-base" />
          <text x={PAD.l + 6} y={y(KEEP_FORK.validation) - 6} className="lc-label lc-label-base">Always keep the fork, validation</text>

          <path d={line("train")} className="lc-train" pathLength={100} />
          <text x={x(1) + 6} y={y(ROUNDS[1].train) - 8} className="lc-label">Best strategy on train</text>

          <path d={steps} className="lc-val" pathLength={100} />
          <text x={x(1) + 6} y={y(ROUNDS[1].validation) + 18} className="lc-label lc-label-val">Incumbent on validation</text>
          {ROUNDS.map((d) => (
            <circle key={d.round} cx={x(d.round)} cy={y(d.validation)} r={d.round === active ? 5.5 : 4} className={`lc-pt ${d.promoted ? "lc-pt-up" : ""}`} />
          ))}
          {ROUNDS.filter((d) => d.promoted).map((d) => (
            <circle key={d.round} cx={x(d.round)} cy={y(d.validation)} r={10} className="lc-promo" />
          ))}

          {ROUNDS.map((d, i) => (
            <rect
              key={d.round}
              x={x(d.round) - (W - PAD.l - PAD.r) / (ROUNDS.length - 1) / 2}
              y={PAD.t}
              width={(W - PAD.l - PAD.r) / (ROUNDS.length - 1)}
              height={H - PAD.t - PAD.b}
              fill="transparent"
              onMouseEnter={() => setActive(i)}
            />
          ))}
        </svg>
      </div>
      <div className="min-w-0">

        <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Rounds">
          <span className="mr-1 t-label text-muted">Pick a round</span>
          {ROUNDS.map((d, i) => (
            <button key={d.round} type="button" onClick={() => setActive(i)} onFocus={() => setActive(i)} aria-pressed={i === active} className={`lc-btn ${d.promoted ? "lc-btn-up" : ""}`}>
              {d.round}
              {d.promoted && <span className="sr-only">, promoted</span>}
            </button>
          ))}
        </div>
        <div key={active} className="fade-swap mt-3 rounded-tile border border-line bg-raised p-3" aria-live="polite">
          <span className={`lc-verdict lc-verdict-${o.tone}`}>Round {r.round}. {o.verdict}</span>
          <dl className="mt-3 grid grid-cols-3 gap-2">
            {o.stats.map(([k, v]) => (
              <div key={k}>
                <dt className="t-label text-muted">{k}</dt>
                <dd className="font-mono text-[15px] font-semibold tabular-nums">{v}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-2 text-[14px] text-muted">{o.line}</p>
        </div>
        <p className="mt-2 text-[12px] leading-[18px] text-faint">Train is {TRAIN_N} NXP hunks and validation is {VALIDATION_N} ST hunks. Validation picks the winner, so its line runs optimistic.</p>
      </div>
    </div>
  );
}
