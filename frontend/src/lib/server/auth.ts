import { cimd } from "@better-auth/cimd";
import { fetchClientMetadataResource } from "@better-auth/cimd/node";
import { mcp } from "@better-auth/mcp";
import { betterAuth } from "better-auth";
import { nextCookies } from "better-auth/next-js";
import { jwt } from "better-auth/plugins";
import { pool } from "./db";
import { appUrl, serverEnv } from "./env";
import { ensureTenant, membership, type Membership } from "./tenants";

/**
 * Better Auth on Postgres. GitHub is the only sign-in method, using the forkward-bot GitHub App's
 * client id and secret (GitHub Apps support user OAuth). Sessions live in the `session` table, so
 * signing out or deleting a row revokes them. The schema is created by migrations/002_better_auth.sql,
 * never at runtime.
 *
 * The same instance is the OAuth 2.1 authorization server for the hosted MCP server: `mcp()` issues
 * access tokens bound to the MCP resource URL and signed with keys from `jwt()`, which publishes them
 * at /api/auth/jwks; `cimd()` lets MCP clients identify themselves with a Client ID Metadata Document.
 */
function build() {
  const baseURL = appUrl();
  return betterAuth({
    appName: "Forkward",
    baseURL,
    basePath: "/api/auth",
    secret: serverEnv("BETTER_AUTH_SECRET"),
    database: pool(),
    emailAndPassword: { enabled: false },
    socialProviders: {
      github: {
        clientId: serverEnv("GITHUB_APP_CLIENT_ID") ?? "",
        clientSecret: serverEnv("GITHUB_APP_CLIENT_SECRET") ?? "",
        // A GitHub App only sees the email when it has the "Email addresses" permission and the user
        // shares one, so fall back to the user's noreply address rather than failing sign-in.
        mapProfileToUser: (profile) => ({
          email: profile.email || `${profile.id}+${profile.login}@users.noreply.github.com`,
        }),
      },
    },
    account: { encryptOAuthTokens: true },
    // The JWT plugin's session-to-JWT endpoint is not needed; MCP clients get tokens from /oauth2/token.
    disabledPaths: ["/token"],
    databaseHooks: {
      user: {
        create: {
          after: async (user) => {
            await ensureTenant(user);
          },
        },
      },
    },
    onAPIError: { errorURL: "/signin" },
    telemetry: { enabled: false },
    plugins: [
      jwt(),
      mcp({
        loginPage: "/signin",
        consentPage: "/consent",
        resource: `${baseURL}/mcp`,
      }),
      cimd({ fetchClientMetadataResource, metadataProfile: "mcp-2026-07-28" }),
      // Lets server actions and route handlers set Better Auth cookies. Must stay last.
      nextCookies(),
    ],
  });
}

export type Auth = ReturnType<typeof build>;

const globalForAuth = globalThis as unknown as { _forkwardAuth?: Auth };

/** Built on first use, so `next build` never needs the database or the secret. */
export function getAuth(): Auth {
  globalForAuth._forkwardAuth ??= build();
  return globalForAuth._forkwardAuth;
}

export type Viewer = {
  user: { id: string; name: string; email: string; image?: string | null };
  sessionId: string;
} & Membership;

/** The signed-in user and their tenant, or null. */
export async function viewer(headers: Headers): Promise<Viewer | null> {
  const s = await getAuth().api.getSession({ headers });
  if (!s) return null;
  const m = (await membership(s.user.id)) ?? (await ensureTenant(s.user));
  return { user: s.user, sessionId: s.session.id, ...m };
}

/** A currently valid GitHub user token for the signed-in user, refreshed by Better Auth when the
 * GitHub App issues expiring tokens. Null when the user has no GitHub account linked. */
export async function githubUserToken(headers: Headers): Promise<string | null> {
  const auth = getAuth();
  const accounts = await auth.api.listUserAccounts({ headers });
  const gh = accounts.find((a) => a.providerId === "github");
  if (!gh) return null;
  const t = await auth.api.getAccessToken({ body: { accountId: gh.id }, headers });
  return t.accessToken ?? null;
}

/** State-changing JSON routes accept only same-origin requests, so a cross-site form cannot drive them. */
export function sameOrigin(req: Request): boolean {
  const origin = req.headers.get("origin");
  if (!origin) return false;
  const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host") ?? new URL(req.url).host;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}
