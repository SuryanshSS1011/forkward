"use client";
import { usePathname } from "next/navigation";
import { useMe } from "./useMe";

/**
 * Hides the app's Nav on the public pages, which carry their own parchment header
 * (MarketingShell). It reads the path on the client so it stays right across client-side
 * navigation, where the root layout is not re-rendered.
 *
 * The landing page, the recorded run and sign-in always use the parchment header, which turns
 * into "Open app" for a signed-in visitor. /billing is pricing when signed out and the Usage page
 * when signed in, so there the Nav waits for the session instead of flashing over the pricing
 * header.
 */
const SHELL_ROUTES = new Set(["/", "/demo", "/signin"]);

export function SiteChrome({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const [me] = useMe();
  if (SHELL_ROUTES.has(path)) return null;
  if (path === "/billing") return me ? <>{children}</> : null;
  return <>{children}</>;
}
