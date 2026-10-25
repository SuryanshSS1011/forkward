"use client";
import { usePathname } from "next/navigation";
import { useMe } from "./useMe";

/**
 * Hides the shared Nav and Footer on the public marketing pages, which carry their own parchment
 * header and footer (src/components/MarketingShell.tsx). It reads the path on the client so it stays
 * right across client-side navigation, where the root layout (and so the Nav/Footer inside it) is
 * not re-rendered.
 *
 * The landing page and the demo chooser are always marketing. The pricing page lives at /billing,
 * which also serves the signed-in Usage workspace, so there the shared chrome is hidden only while
 * signed out (and while the session is still loading, so the parchment pricing never flashes a
 * second header).
 */
// Routes that render their own parchment header/footer via MarketingShell.
const SHELL_ROUTES = new Set(["/", "/demo", "/demo/live", "/signin"]);

export function SiteChrome({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const [me] = useMe();
  if (SHELL_ROUTES.has(path)) return null;
  if (path === "/billing") return me ? <>{children}</> : null;
  return <>{children}</>;
}
