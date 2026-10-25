/** The two halves of the site. Visitors see what Forkward is; signed-in people see their workspace. */
export type NavLink = { href: string; label: string; also?: string[] };

export const PUBLIC_LINKS: NavLink[] = [
  { href: "/demo", label: "Demo" },
  { href: "/how", label: "How it works" },
  { href: "/billing", label: "Pricing" },
];

export const APP_LINKS: NavLink[] = [
  { href: "/repos", label: "Repositories" },
  { href: "/monitor", label: "Activity", also: ["/jobs"] },
  { href: "/connect", label: "Connect" },
  { href: "/keys", label: "API keys" },
  { href: "/billing", label: "Usage" },
];

export function isCurrent(link: NavLink, path: string): boolean {
  const p = path.split("?")[0];
  return [link.href, ...(link.also ?? [])].some((h) => !h.includes("#") && (p === h || p.startsWith(`${h}/`)));
}
