import Link from "next/link";
import { Sym } from "./Sym";

/**
 * The public marketing chrome in the parchment skin: a floating scrap-paper header and a dark
 * footer, wrapped around the page content. Used by the landing page, the demo chooser and the
 * signed-out pricing view, so every public section shares one look. It is a plain component (no
 * server-only imports), so the client-rendered pricing page can use it too.
 *
 * The shared Nav/Footer (src/components/Nav.tsx, Footer.tsx) are hidden on these routes by
 * src/components/SiteChrome.tsx, so this is the only chrome a visitor sees.
 */
export function MarketingShell({
  active,
  children,
}: {
  active?: "demo" | "how" | "pricing";
  children: React.ReactNode;
}) {
  const navlink = "rounded-lg px-2 py-1 transition-colors hover:bg-[var(--pa-surface-high)] hover:text-[var(--pa-ink)]";
  const on = "bg-[var(--pa-surface-high)] font-bold text-[var(--pa-ink)]";
  const off = "text-[var(--pa-ink-muted)]";
  return (
    <div className="parchment flex min-h-screen select-none flex-col">
      <header className="sticky top-0 z-50 px-6 pt-4 lg:px-12">
        <div
          className="mx-auto flex h-20 max-w-7xl items-center justify-between border-2 border-[var(--pa-outline-variant)] bg-[var(--pa-surface)] px-6 shadow-[2px_3px_0px_rgba(56,53,47,0.12)]"
          style={{ borderRadius: "255px 15px 225px 15px/15px 225px 15px 255px" }}
        >
          <Link href="/" className="flex items-center gap-2 text-[var(--pa-primary)]">
            <span className="flex h-9 w-9 items-center justify-center rounded-full border border-[var(--pa-outline)] bg-[var(--pa-primary-fixed)] shadow-[1px_1px_0px_#8a716e]">
              <Sym name="restaurant" className="text-[20px] text-[var(--pa-primary)]" />
            </span>
            <span className="font-display text-[24px] font-bold tracking-tight text-[var(--pa-ink)]">Forkward</span>
          </Link>
          <nav className="hidden items-center gap-4 md:flex">
            <Link href="/demo" className={`${navlink} ${active === "demo" ? on : off}`}>Demo</Link>
            <Link href="/#how" className={`${navlink} ${active === "how" ? on : off}`}>How it works</Link>
            <Link href="/billing" className={`${navlink} ${active === "pricing" ? on : off}`}>Pricing</Link>
          </nav>
          <div className="flex items-center gap-4">
            <Link href="/signin" className="font-label text-[14px] font-bold text-[var(--pa-ink-muted)] underline decoration-[var(--pa-outline-variant)] underline-offset-4 transition-colors hover:text-[var(--pa-ink)] hover:decoration-[var(--pa-ink)]">Sign in</Link>
            <Link
              href="/signin?next=/repos"
              className="border-2 border-[var(--pa-charcoal)] bg-[var(--pa-primary-container)] px-4 py-1 font-label text-[14px] font-bold text-[var(--pa-on-primary)] shadow-[3px_3px_0px_#33302a] transition-all active:translate-x-[2px] active:translate-y-[2px] active:shadow-[1px_1px_0px_#33302a]"
              style={{ borderRadius: "9px 4px 17px 5px / 4px 12px 5px 9px" }}
            >
              Get started
            </Link>
          </div>
        </div>
      </header>

      <main className="flex w-full flex-1 flex-col pb-10 text-[var(--pa-ink)]">{children}</main>

      <footer className="w-full border-t border-dashed border-[var(--pa-outline)] bg-[var(--pa-inverse)] py-10 text-[var(--pa-on-inverse)]">
        <div className="mx-auto max-w-7xl px-6 lg:px-12">
          <div className="flex flex-col items-center justify-between gap-6 border-b border-dashed border-[var(--pa-on-inverse-muted)]/40 pb-6 md:flex-row">
            <div className="flex items-center gap-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-[var(--pa-surface-container)]">
                <Sym name="restaurant" className="text-[18px] text-[var(--pa-inverse)]" />
              </div>
              <span className="font-display text-[20px] font-semibold text-[var(--pa-on-inverse)]">Forkward</span>
            </div>
            <div className="flex flex-wrap items-center justify-center gap-6">
              <Link href="/#how" className="text-[14px] text-[var(--pa-on-inverse-muted)] transition-colors hover:text-[var(--pa-on-inverse)]">How it works</Link>
              <Link href="/demo/live" className="text-[14px] text-[var(--pa-on-inverse-muted)] transition-colors hover:text-[var(--pa-on-inverse)]">Sample run</Link>
              <Link href="/billing" className="text-[14px] text-[var(--pa-on-inverse-muted)] transition-colors hover:text-[var(--pa-on-inverse)]">Pricing</Link>
              <Link href="/signin" className="text-[14px] text-[var(--pa-on-inverse-muted)] transition-colors hover:text-[var(--pa-on-inverse)]">Sign in</Link>
            </div>
          </div>
          <div className="flex flex-col items-center justify-between gap-2 pt-4 font-label text-[11px] text-[var(--pa-on-inverse-muted)] md:flex-row">
            <p>© 2026 Forkward. All rights reserved.</p>
            <p className="font-label text-[14px] text-[var(--pa-tertiary-fixed)]">Parchment edition v1.2</p>
          </div>
        </div>
      </footer>
    </div>
  );
}
