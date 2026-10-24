import Link from "next/link";
import { APP_LINKS, PUBLIC_LINKS, isCurrent } from "@/lib/nav";
import { currentPath, currentViewer } from "@/lib/server/session";
import { AgentGlyph } from "./AgentMark";
import { AuthButton } from "./AuthButton";
import { MobileMenu } from "./MobileMenu";

export async function Nav() {
  const [v, path] = await Promise.all([currentViewer(), currentPath()]);
  const links = v ? APP_LINKS : PUBLIC_LINKS;
  const user = v ? { name: v.user.name || v.user.email, image: v.user.image ?? null } : null;
  return (
    <header className="sticky top-0 z-40 bg-surface px-4 py-3">
      <nav aria-label="Main" className="mx-auto grid max-w-[1080px] grid-cols-[1fr_auto] items-center gap-3 rounded-full border border-line bg-raised px-3 py-2 pl-5 shadow-card md:grid-cols-[auto_1fr_auto]">
        <Link href={v ? "/repos" : "/"} className="nav-logo inline-flex items-center gap-2 text-[20px] font-semibold tracking-tight"><AgentGlyph size={26} />Forkward</Link>
        <ul className="hidden items-center justify-center gap-1 md:flex">
          {links.map((l) => {
            const on = isCurrent(l, path);
            return (
              <li key={l.href}>
                <Link href={l.href} aria-current={on ? "page" : undefined} className={`navlink rounded-full px-4 py-2 text-[15px] font-medium ${on ? "bg-sunken text-ink" : "text-muted hover:bg-sunken hover:text-ink"}`}>{l.label}</Link>
              </li>
            );
          })}
        </ul>
        <div className="flex items-center justify-end gap-2">
          <MobileMenu links={links} />
          <AuthButton user={user} />
        </div>
      </nav>
    </header>
  );
}
