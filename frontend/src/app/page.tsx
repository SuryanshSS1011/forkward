import Link from "next/link";
import { redirect } from "next/navigation";
import { Counter } from "@/components/Counter";
import { MarketingShell } from "@/components/MarketingShell";
import { Sym } from "@/components/Sym";
import { currentViewer } from "@/lib/server/session";

const features: { icon: string; iconWrap: string; h: string; p: string; radius: string }[] = [
  {
    icon: "history",
    iconWrap: "bg-[var(--pa-secondary-fixed)] text-[var(--pa-on-secondary-fixed)]",
    h: "Remembers your fixes",
    p: "Approve a resolution once and Forkward reuses it the next time that change conflicts.",
    radius: "11px 5px 14px 6px",
  },
  {
    icon: "bolt",
    iconWrap: "bg-[var(--pa-primary-fixed)] text-[var(--pa-primary)]",
    h: "Resolves in parallel",
    p: "A team of agents works every conflict at once, about 2.5x faster than one at a time.",
    radius: "8px 14px 6px 12px",
  },
  {
    icon: "calendar_today",
    iconWrap: "bg-[var(--pa-surface-high)] text-[var(--pa-tertiary)]",
    h: "Syncs on your schedule",
    p: "Hourly, daily, or weekly. Set it per repository and forget about it.",
    radius: "12px 6px 15px 5px",
  },
  {
    icon: "lock",
    iconWrap: "bg-[var(--pa-surface-container)] text-[var(--pa-secondary)]",
    h: "Never touches main",
    p: "Every change arrives as a pull request. Nothing ships until you approve it.",
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
  { n: "01", h: "Connect", p: "Point Forkward at the forks you want kept up to date. Takes about a minute.", accent: "secondary", radius: "8px 12px 6px 10px" },
  { n: "02", h: "Resolve", p: "It pulls in upstream and works out a fix for every conflict, so you don't have to.", accent: "secondary", radius: "12px 7px 11px 5px" },
  { n: "03", h: "Check", p: "The fixes it's sure about, it just applies. Anything shaky, it leaves for you.", accent: "primary", radius: "6px 14px 7px 12px" },
  { n: "04", h: "Review", p: "You get a clean pull request to approve, and Forkward learns from the call you make.", accent: "secondary", radius: "10px 6px 14px 8px" },
];

const proof: { to: number; decimals?: number; prefix?: string; suffix?: string; label: string; valueColor: string; radius: string }[] = [
  { to: 76, suffix: "%", label: "of conflicts matched the fix the engineers actually committed.", valueColor: "text-[var(--pa-ink)]", radius: "11px 5px 14px 6px" },
  { to: 2.5, decimals: 1, suffix: "x", label: "faster, with agents resolving conflicts in parallel.", valueColor: "text-[var(--pa-primary)]", radius: "8px 14px 6px 12px" },
  { to: 50, label: "free resolutions to start. No credit card needed.", valueColor: "text-[var(--pa-secondary)]", radius: "12px 6px 15px 5px" },
];

export default async function Landing() {
  // The landing page is for visitors; a signed-in person starts in their workspace.
  if (await currentViewer()) redirect("/repos");
  return (
    <MarketingShell>
      {/* ==================== HERO ==================== */}
      <section className="mx-auto w-full max-w-7xl px-6 pb-14 pt-10 lg:px-12">
        <div className="grid grid-cols-1 items-center gap-10 lg:grid-cols-12">
          {/* Left column */}
          <div className="pa-fade pa-fade-1 flex flex-col lg:col-span-7">
            <div className="relative mb-6">
              <h1 className="font-display text-[34px] font-extrabold leading-tight tracking-tight text-[var(--pa-ink)] md:text-[48px]">
                Every upstream fix, forwarded to your fork.
              </h1>
              <svg className="-mt-1 ml-1 h-4 w-72 text-[var(--pa-primary)]" fill="none" viewBox="0 0 288 16" xmlns="http://www.w3.org/2000/svg">
                <path d="M2.5 11.5C45.2 6.1 112.4 3.8 198.8 7.3C228.6 8.5 264.1 11.2 285.5 13.1" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="4.5" />
              </svg>
            </div>
            <p className="mb-8 max-w-xl text-[19px] leading-relaxed text-[var(--pa-ink-muted)]">
              Forkward merges upstream into your fork, proposes a fix for every conflict, and opens a pull request for you to approve.
            </p>
            <div className="flex flex-wrap items-center gap-4">
              <Link
                href="/signin?next=/repos"
                className="border-2 border-[var(--pa-charcoal)] bg-[var(--pa-primary-container)] px-7 py-3 font-label text-[15px] font-bold text-[var(--pa-on-primary)] shadow-[4px_4px_0px_#33302a] transition-all hover:-translate-y-0.5 active:translate-x-[2px] active:translate-y-[2px] active:shadow-[1px_1px_0px_#33302a]"
                style={{ borderRadius: "8px 16px 6px 14px / 14px 6px 13px 7px" }}
              >
                Get started free
              </Link>
              <Link
                href="/demo/live"
                className="flex items-center gap-2 border-2 border-[var(--pa-outline)] bg-[var(--pa-surface-low)] px-6 py-3 font-label text-[15px] font-bold text-[var(--pa-ink)] shadow-[3px_3px_0px_#8a716e] transition-all hover:-translate-y-0.5 hover:bg-[var(--pa-surface-high)]"
                style={{ borderRadius: "12px 6px 14px 8px / 7px 15px 8px 13px" }}
              >
                <Sym name="play_circle" className="text-[18px] text-[var(--pa-primary)]" />
                Watch it work
              </Link>
              <span className="font-label text-[12px] text-[var(--pa-ink-muted)]">Free to start · 50 resolutions</span>
            </div>
          </div>

          {/* Right column: a simple, legible proposal card */}
          <div className="pa-fade pa-fade-2 relative mt-2 flex justify-center lg:col-span-5 lg:mt-0">
            <div className="absolute -top-3 z-20 h-6 w-32 -rotate-2 bg-[var(--pa-tertiary-fixed)]/70 shadow-sm" style={{ borderRadius: "2px 5px 3px 4px" }} />
            <div className="pa-lift relative z-10 w-full max-w-md border-2 border-[var(--pa-outline)] bg-[var(--pa-surface)] p-5 shadow-[5px_5px_0px_#8a716e]" style={{ borderRadius: "12px 5px 16px 8px / 6px 15px 7px 14px" }}>
              <div className="flex items-center justify-between border-b border-dashed border-[var(--pa-outline-variant)] pb-3">
                <div className="flex items-center gap-2">
                  <div className="flex h-9 w-9 items-center justify-center rounded-full border border-[var(--pa-outline)] bg-[var(--pa-primary-fixed)]">
                    <Sym name="smart_toy" className="text-[22px] text-[var(--pa-primary)]" />
                  </div>
                  <div>
                    <p className="font-label text-[11px] uppercase tracking-wider text-[var(--pa-ink-muted)]">Forkward proposal</p>
                    <p className="font-label text-[13px] font-bold text-[var(--pa-ink)]">attention.rs</p>
                  </div>
                </div>
                <div className="flex items-center gap-1 rounded border border-[#c3e6cb] bg-[#d4edda] px-2 py-0.5 text-[#155724]" style={{ borderRadius: "4px 7px 3px 6px" }}>
                  <Sym name="check_circle" className="text-[14px]" />
                  <span className="font-label text-[11px] font-bold">Resolved</span>
                </div>
              </div>
              <div className="my-3 grid grid-cols-2 gap-2 font-label text-[11px]">
                <div className="rounded-sm border-l-2 border-[var(--pa-secondary)] bg-[var(--pa-surface-low)] p-2">
                  <span className="mb-1 block font-bold text-[var(--pa-secondary)]">Your fork</span>
                  <pre className="overflow-x-auto text-[10px] leading-tight text-[var(--pa-ink)]"><code>{`let attn =
  scores.softmax();`}</code></pre>
                </div>
                <div className="rounded-sm border-l-2 border-[var(--pa-primary)] bg-[var(--pa-surface-low)] p-2">
                  <span className="mb-1 block font-bold text-[var(--pa-primary)]">Upstream fix</span>
                  <pre className="overflow-x-auto text-[10px] leading-tight text-[var(--pa-ink)]"><code>{`let attn =
  scores.softmax_fp16();`}</code></pre>
                </div>
              </div>
              <div className="flex items-start gap-2 rounded-md border-2 border-[var(--pa-outline-variant)] bg-[var(--pa-surface-high)] p-3 shadow-[2px_2px_0px_#ddc0bc]">
                <Sym name="auto_fix_high" className="text-[18px] text-[var(--pa-primary)]" />
                <p className="text-[14px] leading-snug text-[var(--pa-ink)]">
                  <span className="font-bold">Forkward took upstream's fix.</span> It was confident, so it applied it and moved on.
                </p>
              </div>
              <div className="mt-3 flex items-center justify-between font-label text-[11px] text-[var(--pa-ink-muted)]">
                <span>1 of 3 conflicts in this sync</span>
                <Link href="/demo/live" className="font-bold text-[var(--pa-primary)] underline hover:text-[var(--pa-ink)]">See the full run</Link>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ==================== WORKS WITH (inline row) ==================== */}
      <section className="mx-auto w-full max-w-7xl px-6 py-8 lg:px-12">
        <div className="flex flex-wrap items-center justify-center gap-3">
          <span className="mr-1 font-display text-[22px] font-bold text-[var(--pa-ink)] md:text-[26px]">Works with</span>
          {tools.map((t) => (
            <div key={t.name} className="pa-lift flex items-center gap-1.5 border-2 border-[var(--pa-outline-variant)] bg-[var(--pa-surface)] px-4 py-2 shadow-[2px_2px_0px_#8a716e]" style={{ borderRadius: "6px 9px 5px 8px" }}>
              <Sym name={t.icon} className={`text-[16px] ${t.iconColor}`} />
              <span className="font-label text-[14px] font-bold">{t.name}</span>
            </div>
          ))}
        </div>
      </section>

      {/* ==================== HOW IT WORKS ==================== */}
      <section id="how" className="mx-auto w-full max-w-7xl scroll-mt-24 px-6 py-12 lg:px-12">
        <div className="relative border-2 border-[var(--pa-outline)] bg-[var(--pa-surface-high)] p-8 shadow-[4px_4px_0px_#8a716e] md:p-10" style={{ borderRadius: "15px 7px 20px 9px / 9px 18px 8px 16px" }}>
          <div className="mb-8 text-center">
            <span className="font-label text-[11px] font-bold uppercase tracking-widest text-[var(--pa-primary)]">How it works</span>
            <h2 className="mt-1 font-display text-[30px] font-extrabold text-[var(--pa-ink)] md:text-[36px]">Four steps, then it runs itself.</h2>
          </div>
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {steps.map((s) => {
              const ring = s.accent === "primary"
                ? "border-[var(--pa-primary)] bg-[var(--pa-primary-fixed)]/50 text-[var(--pa-primary)]"
                : "border-[var(--pa-secondary)] bg-[var(--pa-secondary-fixed)]/50 text-[var(--pa-secondary)]";
              return (
                <div key={s.n} className="pa-lift border-2 border-[var(--pa-outline-variant)] bg-[var(--pa-surface)] p-5 shadow-[2px_2px_0px_#ddc0bc]" style={{ borderRadius: s.radius }}>
                  <div className={`mb-3 flex h-9 w-9 items-center justify-center rounded-full border-2 font-label text-[13px] font-bold ${ring}`}>{s.n}</div>
                  <h3 className="mb-1 font-display text-[20px] font-bold text-[var(--pa-ink)]">{s.h}</h3>
                  <p className="text-[14px] leading-relaxed text-[var(--pa-ink-muted)]">{s.p}</p>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* ==================== FEATURES ==================== */}
      <section className="mx-auto w-full max-w-7xl px-6 py-12 lg:px-12">
        <div className="mb-8 max-w-2xl">
          <span className="font-label text-[11px] font-bold uppercase tracking-wider text-[var(--pa-secondary)]">Why teams use it</span>
          <h2 className="mt-1 font-display text-[30px] font-extrabold text-[var(--pa-ink)] md:text-[36px]">Built to stay out of your way.</h2>
        </div>
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
          {features.map((f) => (
            <div key={f.h} className="pa-lift border-2 border-[var(--pa-outline-variant)] bg-[var(--pa-surface)] p-5 shadow-[3px_3px_0px_#ddc0bc]" style={{ borderRadius: f.radius }}>
              <div className={`mb-3 flex h-10 w-10 items-center justify-center rounded-full ${f.iconWrap}`}>
                <Sym name={f.icon} className="text-[20px]" />
              </div>
              <h3 className="mb-1 font-display text-[19px] font-bold text-[var(--pa-ink)]">{f.h}</h3>
              <p className="text-[14px] leading-relaxed text-[var(--pa-ink-muted)]">{f.p}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ==================== PROOF ==================== */}
      <section className="mx-auto w-full max-w-7xl px-6 py-12 lg:px-12">
        <div className="grid grid-cols-1 gap-8 md:grid-cols-3">
          {proof.map((p) => (
            <div key={p.label} className="pa-lift relative border-2 border-[var(--pa-outline)] bg-[var(--pa-surface)] p-6 text-center shadow-[4px_4px_0px_#8a716e]" style={{ borderRadius: p.radius }}>
              <Counter to={p.to} decimals={p.decimals} prefix={p.prefix} suffix={p.suffix} className={`block font-display text-[52px] font-extrabold leading-none tracking-tight ${p.valueColor}`} />
              <p className="mx-auto mt-3 max-w-[26ch] text-[15px] leading-relaxed text-[var(--pa-ink-muted)]">{p.label}</p>
            </div>
          ))}
        </div>
        <p className="mt-6 text-center">
          <Link href="/demo/live" className="font-label text-[14px] font-bold text-[var(--pa-primary)] underline hover:text-[var(--pa-ink)]">See a recorded run</Link>
        </p>
      </section>

      {/* ==================== CLOSING ==================== */}
      <section className="mx-auto w-full max-w-7xl px-6 pb-16 pt-8 lg:px-12">
        <div className="flex flex-col items-center justify-center border-2 border-[var(--pa-outline)] bg-[var(--pa-surface-container)] p-10 text-center shadow-[5px_5px_0px_#8a716e]" style={{ borderRadius: "18px 8px 24px 10px / 10px 22px 9px 19px" }}>
          <h2 className="max-w-2xl font-display text-[32px] font-extrabold leading-tight tracking-tight text-[var(--pa-primary)] md:text-[44px]">
            Keep your fork up to date without losing your weekend.
          </h2>
          <p className="mb-8 mt-4 max-w-md text-[18px] text-[var(--pa-ink-muted)]">
            Connect a fork and see your first sync in minutes.
          </p>
          <Link
            href="/signin?next=/repos"
            className="border-2 border-[var(--pa-charcoal)] bg-[var(--pa-primary-container)] px-10 py-4 font-label text-[18px] font-bold text-[var(--pa-on-primary)] shadow-[4px_4px_0px_#33302a] transition-all hover:-translate-y-0.5 active:translate-x-[2px] active:translate-y-[2px] active:shadow-[1px_1px_0px_#33302a]"
            style={{ borderRadius: "8px 18px 6px 14px / 14px 6px 15px 7px" }}
          >
            Get started free
          </Link>
          <span className="mt-4 font-label text-[11px] text-[var(--pa-ink-muted)]">No credit card needed · 50 free resolutions</span>
        </div>
      </section>
    </MarketingShell>
  );
}
