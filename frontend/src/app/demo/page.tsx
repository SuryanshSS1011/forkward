import Link from "next/link";
import { MarketingShell } from "@/components/MarketingShell";
import { Sym } from "@/components/Sym";
import { currentViewer } from "@/lib/server/session";

export const metadata = { title: "Try Forkward" };

const options: {
  icon: string;
  iconWrap: string;
  h: (signedIn: boolean) => string;
  p: string;
  href: (signedIn: boolean) => string;
  cta: (signedIn: boolean) => string;
  radius: string;
}[] = [
  {
    icon: "history",
    iconWrap: "bg-[var(--pa-secondary-fixed)] text-[var(--pa-on-secondary-fixed)]",
    h: () => "Watch a real sync",
    p: "See Forkward handle a real fork end to end, five conflicts and all. No sign-up needed.",
    href: () => "/demo/live",
    cta: () => "Open the sample sync",
    radius: "11px 5px 14px 6px",
  },
  {
    icon: "cable",
    iconWrap: "bg-[var(--pa-primary-fixed)] text-[var(--pa-primary)]",
    h: (s) => (s ? "Your repositories" : "Try it on your fork"),
    p: "Connect your own fork and preview a sync before anything is pushed. Free to start, 50 resolutions included.",
    href: (s) => (s ? "/repos" : "/signin?next=/repos"),
    cta: (s) => (s ? "Open your repositories" : "Sign in and connect a fork"),
    radius: "8px 14px 6px 12px",
  },
];

export default async function Demo() {
  const signedIn = Boolean(await currentViewer());
  return (
    <MarketingShell active="demo">
      <section className="mx-auto w-full max-w-7xl px-6 pb-10 pt-12 lg:px-12 md:pt-16">
        <h1 className="max-w-[20ch] font-display text-[32px] font-extrabold tracking-tight text-[var(--pa-ink)] md:text-[44px]">How do you want to start?</h1>
        <p className="mt-3 max-w-[52ch] text-[18px] leading-relaxed text-[var(--pa-ink-muted)]">
          Watch a recorded run, or connect your own fork and preview a sync before anything is pushed.
        </p>
        <div className="mt-10 grid gap-8 md:grid-cols-2">
          {options.map((o) => (
            <section
              key={o.h(signedIn)}
              className="pa-lift flex flex-col items-start border-2 border-[var(--pa-outline)] bg-[var(--pa-surface)] p-6 shadow-[4px_4px_0px_#8a716e]"
              style={{ borderRadius: o.radius }}
            >
              <div className={`mb-4 flex h-11 w-11 items-center justify-center rounded-full ${o.iconWrap}`}>
                <Sym name={o.icon} className="text-[22px]" />
              </div>
              <h2 className="font-display text-[24px] font-bold text-[var(--pa-ink)]">{o.h(signedIn)}</h2>
              <p className="mb-6 mt-2 max-w-[42ch] leading-relaxed text-[var(--pa-ink-muted)]">{o.p}</p>
              <Link
                href={o.href(signedIn)}
                className="mt-auto border-2 border-[var(--pa-charcoal)] bg-[var(--pa-primary-container)] px-6 py-2 font-label text-[14px] font-bold text-[var(--pa-on-primary)] shadow-[4px_4px_0px_#33302a] transition-all hover:-translate-y-0.5 active:translate-x-[2px] active:translate-y-[2px] active:shadow-[1px_1px_0px_#33302a]"
                style={{ borderRadius: "8px 16px 6px 14px / 14px 6px 13px 7px" }}
              >
                {o.cta(signedIn)}
              </Link>
            </section>
          ))}
        </div>
      </section>
    </MarketingShell>
  );
}
