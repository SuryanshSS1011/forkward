import { NextResponse, type NextRequest } from "next/server";

/** Workspace pages, which only make sense signed in. */
const SIGNED_IN = ["/repos", "/monitor", "/connect", "/keys", "/jobs"];

/**
 * Cheap routing by sign-in state. It sends a signed-in person from the landing page to the
 * workspace, and a visitor with no session cookie straight from a workspace page to
 * sign in, with a real redirect. And it passes the requested path to server components, which
 * cannot read it otherwise. The (app) layout still checks the session itself, so a stale cookie
 * gets the same treatment.
 */
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const gated = SIGNED_IN.some((p) => pathname === p || pathname.startsWith(`${p}/`));
  const hasSession = request.cookies.getAll().some((c) => c.name.endsWith("session_token"));
  if (gated && !hasSession) {
    const to = new URL("/signin", request.url);
    to.searchParams.set("next", pathname + search);
    return NextResponse.redirect(to);
  }
  // A signed-in person starts in their workspace rather than on the landing page.
  if (pathname === "/" && hasSession) return NextResponse.redirect(new URL("/repos", request.url));
  const headers = new Headers(request.headers);
  headers.set("x-forkward-path", pathname + search);
  return NextResponse.next({ request: { headers } });
}

export const config = {
  matcher: ["/((?!api/|_next/|favicon|icon|logos/|.*\\.(?:svg|png|webp|jpg|ico|woff2?)$).*)"],
};
