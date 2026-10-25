import Link from "next/link";
import { AgentGlyph } from "./AgentMark";
import { MarketingAuth } from "./MarketingAuth";
import { MobileMenu } from "./MobileMenu";

export const MARKETING_LINKS = [
  { href: "/demo", label: "Demo" },
  { href: "/#how", label: "How it works" },
  { href: "/billing", label: "Pricing" },
] as const;

/**
 * The public chrome in the parchment skin: one floating header around the page content. Used by
 * the landing page, the recorded run and the signed-out pricing view. It has no server-only
 * imports, so the client-rendered pricing page can use it too. The app's Nav is hidden on these
 * routes by SiteChrome, so this is the only header a visitor sees.
 *
 * `signedIn` is the server's answer when the page knows it, so the header renders the right
 * action without waiting for the session; `auth={false}` drops the action on the sign-in page.
 */
export function MarketingShell({
  active,
  signedIn,
  auth = true,
  children,
}: {
  active?: "demo" | "how" | "pricing";
  signedIn?: boolean;
  auth?: boolean;
  children: React.ReactNode;
}) {
  const navlink = "rounded-lg px-2 py-1 transition-colors hover:bg-[var(--pa-surface-high)] hover:text-[var(--pa-ink)]";
  const on = "bg-[var(--pa-surface-high)] font-bold text-[var(--pa-ink)]";
  const off = "text-[var(--pa-ink-muted)]";
  const current = { demo: "/demo", how: "/#how", pricing: "/billing" }[active ?? "demo"];
  return (
    <div className="parchment flex min-h-screen flex-col">
      <header className="sticky top-0 z-50 px-4 pt-4 sm:px-6 lg:px-12">
        <div
          className="mx-auto flex h-16 max-w-7xl items-center justify-between gap-3 border-2 border-[var(--pa-outline-variant)] bg-[var(--pa-surface)] px-4 shadow-[2px_3px_0px_rgba(56,53,47,0.12)] sm:h-20 sm:px-6"
          style={{ borderRadius: "255px 15px 225px 15px/15px 225px 15px 255px" }}
        >
          <Link href="/" className="flex items-center gap-2 text-[var(--pa-ink)]">
            <AgentGlyph size={30} />
            <span className="font-display text-[22px] font-bold tracking-tight sm:text-[24px]">Forkward</span>
          </Link>
          <nav aria-label="Main" className="hidden items-center gap-4 md:flex">
            {MARKETING_LINKS.map((l) => (
              <Link key={l.href} href={l.href} aria-current={active && l.href === current ? "page" : undefined} className={`${navlink} ${active && l.href === current ? on : off}`}>
                {l.label}
              </Link>
            ))}
          </nav>
          <div className="flex items-center gap-2">
            <MobileMenu links={[...MARKETING_LINKS]} />
            {auth && <MarketingAuth signedIn={signedIn} />}
          </div>
        </div>
      </header>

      <div className="flex w-full flex-1 flex-col pb-16 text-[var(--pa-ink)]">{children}</div>
    </div>
  );
}
