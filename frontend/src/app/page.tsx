import Link from "next/link";
import { redirect } from "next/navigation";
import { Counter } from "@/components/Counter";
import { GitHubMark } from "@/components/MarketingAuth";
import { MarketingShell } from "@/components/MarketingShell";
import { Sym } from "@/components/Sym";
import { SAMPLE_JOB } from "@/lib/demo/sample";
import { PLANS } from "@/lib/plans";
import { currentViewer } from "@/lib/server/session";

const FREE_UNITS = PLANS.trial.monthly_units.toLocaleString("en-US");

// The hero shows one real hunk from the recorded run on /demo: the one where both sides were kept.
const HERO = SAMPLE_JOB.hunks.find((h) => h.file === "src/devices.c") ?? SAMPLE_JOB.hunks[0];
const HERO_CHOSEN = HERO.detail?.candidates?.find((c) => c.chosen);
const HERO_RULES = (HERO_CHOSEN?.sources ?? []).filter((s) => !s.startsWith("model:")).map((s) => s.replaceAll("_", " "));
const HERO_MODELS = (HERO_CHOSEN?.sources ?? []).filter((s) => s.startsWith("model:")).length;
const RUN_SECONDS = (Date.parse(SAMPLE_JOB.updated_at) - Date.parse(SAMPLE_JOB.created_at)) / 1000;

const features: { icon: string; iconWrap: string; h: string; p: string; radius: string }[] = [
  {
    icon: "history",
    iconWrap: "bg-[var(--pa-secondary-fixed)] text-[var(--pa-on-secondary-fixed)]",
    h: "Remembers your fixes",
    p: "Approve a resolution once and Forkward reuses it the next time the same upstream change conflicts.",
    radius: "11px 5px 14px 6px",
  },
  {
    icon: "groups",
    iconWrap: "bg-[var(--pa-primary-fixed)] text-[var(--pa-primary)]",
    h: "Many proposers, one vote",
    p: "Deterministic rules and several AI proposers suggest a fix for every conflict, and they have to agree.",
    radius: "8px 14px 6px 12px",
  },
  {
    icon: "calendar_today",
    iconWrap: "bg-[var(--pa-surface-high)] text-[var(--pa-tertiary)]",
    h: "Syncs on your schedule",
    p: "Every hour, day or week, set per repository, or whenever you press Sync.",
    radius: "12px 6px 15px 5px",
  },
  {
    icon: "lock",
    iconWrap: "bg-[var(--pa-surface-container)] text-[var(--pa-secondary)]",
    h: "Never touches main",
    p: "Every sync arrives on a new branch as a pull request. Nothing ships until you approve it.",
    radius: "9px 13px 7px 11px",
  },
];

const tools: { icon: string; iconColor: string; name: string }[] = [
  { icon: "terminal", iconColor: "text-[var(--pa-ink)]", name: "GitHub" },
  { icon: "psychology", iconColor: "text-[var(--pa-primary)]", name: "Claude Code" },
  { icon: "near_me", iconColor: "text-[var(--pa-secondary)]", name: "Cursor" },
  { icon: "code", iconColor: "text-[var(--pa-ink-muted)]", name: "VS Code" },
  { icon: "hub", iconColor: "text-[var(--pa-tertiary)]", name: "MCP clients" },
];

const steps: { n: string; h: string; p: string; accent: "secondary" | "primary"; radius: string }[] = [
  { n: "01", h: "Connect", p: "Sign in with GitHub and install the Forkward app on the forks you want kept up to date.", accent: "secondary", radius: "8px 12px 6px 10px" },
  { n: "02", h: "Resolve", p: "It merges upstream on a new branch and proposes a fix for every conflict.", accent: "secondary", radius: "12px 7px 11px 5px" },
  { n: "03", h: "Check", p: "A safety gate checks each fix. Until it is certified for a model, every fix waits for you.", accent: "primary", radius: "6px 14px 7px 12px" },
  { n: "04", h: "Review", p: "You approve the pull request hunk by hunk, and Forkward learns from what you keep.", accent: "secondary", radius: "10px 6px 14px 8px" },
];

const proof: { to: number; decimals?: number; suffix?: string; label: string; source: string; valueColor: string; radius: string }[] = [
  {
    to: 76,
    suffix: "%",
    label: "of held-out kernel conflicts matched what the vendor's engineers committed (54 of 71).",
    source: "Rockchip test split, DeepSeek V4.1 Flash on Luminal, evaluated once before this event. Always keeping the vendor side scores 71.8%.",
    valueColor: "text-[var(--pa-ink)]",
    radius: "11px 5px 14px 6px",
  },
  {
    to: RUN_SECONDS,
    decimals: 1,
    suffix: " s",
    label: `from fetching upstream to every conflict resolved, in the recorded run of ${SAMPLE_JOB.hunks.length} conflicts.`,
    source: "DeepSeek V4.1 Flash on Vultr, preview only.",
    valueColor: "text-[var(--pa-primary)]",
    radius: "8px 14px 6px 12px",
  },
  {
    to: PLANS.trial.monthly_units,
    label: "free units a month. One unit is one conflict resolved on the default model.",
    source: "No card needed.",
    valueColor: "text-[var(--pa-secondary)]",
    radius: "12px 6px 15px 5px",
  },
];

const primaryButton =
  "inline-flex items-center gap-2 border-2 border-[var(--pa-charcoal)] bg-[var(--pa-primary-container)] px-7 py-3 font-label text-[15px] font-bold text-[var(--pa-on-primary)] shadow-[4px_4px_0px_#33302a] transition-all hover:-translate-y-0.5 active:translate-x-[2px] active:translate-y-[2px] active:shadow-[1px_1px_0px_#33302a]";

export default async function Landing() {
  // The landing page is for visitors; a signed-in person starts in their workspace.
  if (await currentViewer()) redirect("/repos");
  return (
    <MarketingShell signedIn={false}>
      {/* Hero */}
      <section className="mx-auto w-full max-w-7xl px-6 pb-14 pt-10 lg:px-12">
        <div className="grid grid-cols-1 items-center gap-10 lg:grid-cols-12">
          <div className="pa-fade pa-fade-1 flex flex-col lg:col-span-7">
            <div className="relative mb-6">
              <h1 className="font-display text-[34px] font-extrabold leading-tight tracking-tight text-[var(--pa-ink)] md:text-[48px]">
                Every upstream fix, forwarded to your fork.
              </h1>
              <svg aria-hidden className="-mt-1 ml-1 h-4 w-72 max-w-full text-[var(--pa-primary)]" fill="none" viewBox="0 0 288 16" xmlns="http://www.w3.org/2000/svg">
                <path d="M2.5 11.5C45.2 6.1 112.4 3.8 198.8 7.3C228.6 8.5 264.1 11.2 285.5 13.1" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="4.5" />
              </svg>
            </div>
            <p className="mb-8 max-w-xl text-[19px] leading-relaxed text-[var(--pa-ink-muted)]">
              Forkward merges upstream into your fork, proposes a fix for every conflict, and opens a pull request for you to approve.
            </p>
            <div className="flex flex-wrap items-center gap-4">
              <Link href="/signin?next=/repos" className={primaryButton} style={{ borderRadius: "8px 16px 6px 14px / 14px 6px 13px 7px" }}>
                <GitHubMark size={18} />
                Start with GitHub
              </Link>
              <Link
                href="/demo"
                className="flex items-center gap-2 border-2 border-[var(--pa-outline)] bg-[var(--pa-surface-low)] px-6 py-3 font-label text-[15px] font-bold text-[var(--pa-ink)] shadow-[3px_3px_0px_#8a716e] transition-all hover:-translate-y-0.5 hover:bg-[var(--pa-surface-high)]"
                style={{ borderRadius: "12px 6px 14px 8px / 7px 15px 8px 13px" }}
              >
                <Sym name="play_circle" className="text-[18px] text-[var(--pa-primary)]" />
                See a real run
              </Link>
            </div>
            <p className="mt-4 font-label text-[12px] text-[var(--pa-ink-muted)]">{FREE_UNITS} free units a month. No card needed.</p>
          </div>

          {/* One real hunk from the recorded run */}
          <div className="pa-fade pa-fade-2 relative mt-2 flex justify-center lg:col-span-5 lg:mt-0">
            <div className="absolute -top-3 z-20 h-6 w-32 -rotate-2 bg-[var(--pa-tertiary-fixed)]/70 shadow-sm" style={{ borderRadius: "2px 5px 3px 4px" }} />
            <figure className="pa-lift relative z-10 w-full max-w-md border-2 border-[var(--pa-outline)] bg-[var(--pa-surface)] p-5 shadow-[5px_5px_0px_#8a716e]" style={{ borderRadius: "12px 5px 16px 8px / 6px 15px 7px 14px" }}>
              <div className="flex items-center justify-between gap-3 border-b border-dashed border-[var(--pa-outline-variant)] pb-3">
                <div className="flex min-w-0 items-center gap-2">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-[var(--pa-outline)] bg-[var(--pa-primary-fixed)]">
                    <Sym name="merge" className="text-[22px] text-[var(--pa-primary)]" />
                  </div>
                  <div className="min-w-0">
                    <p className="font-label text-[11px] uppercase tracking-wider text-[var(--pa-ink-muted)]">Forkward proposal</p>
                    <p className="truncate font-label text-[13px] font-bold text-[var(--pa-ink)]">{HERO.file}</p>
                  </div>
                </div>
                <span className="shrink-0 rounded border border-[var(--pa-outline-variant)] bg-[var(--pa-surface-high)] px-2 py-0.5 font-label text-[11px] font-bold text-[var(--pa-ink)]" style={{ borderRadius: "4px 7px 3px 6px" }}>
                  {HERO.decision === "auto" ? "Applied" : "Waiting for you"}
                </span>
              </div>
              <div className="my-3 grid grid-cols-2 gap-2 font-label text-[11px]">
                <div className="min-w-0 rounded-sm border-l-2 border-[var(--pa-secondary)] bg-[var(--pa-surface-low)] p-2">
                  <span className="mb-1 block font-bold text-[var(--pa-secondary)]">Your fork</span>
                  <pre className="overflow-x-auto text-[10px] leading-tight text-[var(--pa-ink)]"><code>{(HERO.detail?.vendor ?? []).join("\n")}</code></pre>
                </div>
                <div className="min-w-0 rounded-sm border-l-2 border-[var(--pa-primary)] bg-[var(--pa-surface-low)] p-2">
                  <span className="mb-1 block font-bold text-[var(--pa-primary)]">Upstream</span>
                  <pre className="overflow-x-auto text-[10px] leading-tight text-[var(--pa-ink)]"><code>{(HERO.detail?.upstream ?? []).join("\n")}</code></pre>
                </div>
              </div>
              <div className="rounded-sm border-l-2 border-[var(--pa-tertiary)] bg-[var(--pa-surface-low)] p-2 font-label text-[11px]">
                <span className="mb-1 block font-bold text-[var(--pa-tertiary)]">Proposed: keep both</span>
                <pre className="overflow-x-auto text-[10px] leading-tight text-[var(--pa-ink)]"><code>{HERO.resolution}</code></pre>
              </div>
              <figcaption className="mt-3 flex items-start gap-2 rounded-md border-2 border-[var(--pa-outline-variant)] bg-[var(--pa-surface-high)] p-3 shadow-[2px_2px_0px_#ddc0bc]">
                <Sym name="how_to_vote" className="text-[18px] text-[var(--pa-primary)]" />
                <p className="text-[14px] leading-snug text-[var(--pa-ink)]">
                  <span className="font-bold">
                    {HERO_MODELS} AI proposers{HERO_RULES.length ? ` and the "${HERO_RULES[0]}" rule` : ""} agreed.
                  </span>{" "}
                  Confidence {Math.round((HERO.confidence ?? 0) * 100)}%. It waits for you because the gate is not yet certified for this model.
                </p>
              </figcaption>
              <div className="mt-3 flex items-center justify-between font-label text-[11px] text-[var(--pa-ink-muted)]">
                <span>1 of {SAMPLE_JOB.hunks.length} conflicts in a recorded run</span>
                <Link href="/demo" className="font-bold text-[var(--pa-primary)] underline hover:text-[var(--pa-ink)]">See the full run</Link>
              </div>
            </figure>
          </div>
        </div>
      </section>

      {/* Works with */}
      <section className="mx-auto w-full max-w-7xl px-6 py-8 lg:px-12">
        <div className="flex flex-wrap items-center justify-center gap-3">
          <h2 className="mr-1 font-display text-[22px] font-bold text-[var(--pa-ink)] md:text-[26px]">Works with</h2>
          <ul className="flex flex-wrap items-center justify-center gap-3">
            {tools.map((t) => (
              <li key={t.name} className="flex items-center gap-1.5 border-2 border-[var(--pa-outline-variant)] bg-[var(--pa-surface)] px-4 py-2 shadow-[2px_2px_0px_#8a716e]" style={{ borderRadius: "6px 9px 5px 8px" }}>
                <Sym name={t.icon} className={`text-[16px] ${t.iconColor}`} />
                <span className="font-label text-[14px] font-bold">{t.name}</span>
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* How it works */}
      <section id="how" className="mx-auto w-full max-w-7xl scroll-mt-28 px-6 py-12 lg:px-12">
        <div className="relative border-2 border-[var(--pa-outline)] bg-[var(--pa-surface-high)] p-6 shadow-[4px_4px_0px_#8a716e] md:p-10" style={{ borderRadius: "15px 7px 20px 9px / 9px 18px 8px 16px" }}>
          <div className="mb-8 text-center">
            <span className="font-label text-[11px] font-bold uppercase tracking-widest text-[var(--pa-primary)]">How it works</span>
            <h2 className="mt-1 font-display text-[30px] font-extrabold text-[var(--pa-ink)] md:text-[36px]">Four steps, then it runs on a schedule.</h2>
          </div>
          <ol className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {steps.map((s) => {
              const ring = s.accent === "primary"
                ? "border-[var(--pa-primary)] bg-[var(--pa-primary-fixed)]/50 text-[var(--pa-primary)]"
                : "border-[var(--pa-secondary)] bg-[var(--pa-secondary-fixed)]/50 text-[var(--pa-secondary)]";
              return (
                <li key={s.n} className="border-2 border-[var(--pa-outline-variant)] bg-[var(--pa-surface)] p-5 shadow-[2px_2px_0px_#ddc0bc]" style={{ borderRadius: s.radius }}>
                  <div className={`mb-3 flex h-9 w-9 items-center justify-center rounded-full border-2 font-label text-[13px] font-bold ${ring}`} aria-hidden>{s.n}</div>
                  <h3 className="mb-1 font-display text-[20px] font-bold text-[var(--pa-ink)]">{s.h}</h3>
                  <p className="text-[14px] leading-relaxed text-[var(--pa-ink-muted)]">{s.p}</p>
                </li>
              );
            })}
          </ol>
        </div>
      </section>

      {/* Features */}
      <section className="mx-auto w-full max-w-7xl px-6 py-12 lg:px-12">
        <div className="mb-8 max-w-2xl">
          <span className="font-label text-[11px] font-bold uppercase tracking-wider text-[var(--pa-secondary)]">Why teams use it</span>
          <h2 className="mt-1 font-display text-[30px] font-extrabold text-[var(--pa-ink)] md:text-[36px]">Built to stay out of your way.</h2>
        </div>
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
          {features.map((f) => (
            <div key={f.h} className="border-2 border-[var(--pa-outline-variant)] bg-[var(--pa-surface)] p-5 shadow-[3px_3px_0px_#ddc0bc]" style={{ borderRadius: f.radius }}>
              <div className={`mb-3 flex h-10 w-10 items-center justify-center rounded-full ${f.iconWrap}`}>
                <Sym name={f.icon} className="text-[20px]" />
              </div>
              <h3 className="mb-1 font-display text-[19px] font-bold text-[var(--pa-ink)]">{f.h}</h3>
              <p className="text-[14px] leading-relaxed text-[var(--pa-ink-muted)]">{f.p}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Proof */}
      <section className="mx-auto w-full max-w-7xl px-6 py-12 lg:px-12">
        <h2 className="sr-only">Measured results</h2>
        <div className="grid grid-cols-1 gap-8 md:grid-cols-3">
          {proof.map((p) => (
            <div key={p.label} className="relative border-2 border-[var(--pa-outline)] bg-[var(--pa-surface)] p-6 text-center shadow-[4px_4px_0px_#8a716e]" style={{ borderRadius: p.radius }}>
              <Counter to={p.to} decimals={p.decimals} suffix={p.suffix} className={`block font-display text-[52px] font-extrabold leading-none tracking-tight ${p.valueColor}`} />
              <p className="mx-auto mt-3 max-w-[30ch] text-[15px] leading-relaxed text-[var(--pa-ink)]">{p.label}</p>
              <p className="mx-auto mt-2 max-w-[34ch] font-label text-[11px] leading-relaxed text-[var(--pa-ink-muted)]">{p.source}</p>
            </div>
          ))}
        </div>
      </section>
    </MarketingShell>
  );
}
