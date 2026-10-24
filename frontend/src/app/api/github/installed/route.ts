import { NextResponse } from "next/server";
import { githubUserToken, viewer } from "@/lib/server/auth";
import { withTenant } from "@/lib/server/db";
import { appUrl } from "@/lib/server/env";
import { canManageInstallation, forkUpstream, installationRepositories, userInstallations, type Repository } from "@/lib/server/github";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** How many forks get their upstream looked up per installation, to bound GitHub calls. */
const MAX_UPSTREAM_LOOKUPS = 30;

/**
 * The GitHub App's Setup URL: GitHub sends the user here after they install or reconfigure the
 * forkward-bot App. The query string is attacker-controlled, so nothing is recorded until the
 * signed-in user's own GitHub token shows they own that installation's account or administer its
 * organization. Then the installation
 * and the repositories it grants are upserted under the user's tenant, with an audit row.
 */
export async function GET(req: Request) {
  const base = appUrl();
  const back = (status: string) => NextResponse.redirect(`${base}/repos?github=${status}`);
  const q = new URL(req.url).searchParams;
  const action = q.get("setup_action") ?? "install";
  if (action === "request") return back("requested");
  const raw = q.get("installation_id") ?? "";
  if (!/^\d{1,19}$/.test(raw)) return back("invalid");
  const installationId = Number(raw);

  const v = await viewer(req.headers);
  if (!v) {
    const here = `/api/github/installed?${new URLSearchParams({ installation_id: raw, setup_action: action })}`;
    return NextResponse.redirect(`${base}/signin?next=${encodeURIComponent(here)}`);
  }
  if (v.role !== "owner") return back("not-owner");

  let installation;
  let repos: (Repository & { upstream: string | null })[];
  try {
    const token = await githubUserToken(req.headers);
    if (!token) return back("signin-github");
    installation = (await userInstallations(token)).find((i) => i.id === installationId);
    if (!installation) return back("not-yours");
    if (!(await canManageInstallation(token, installation))) return back("not-admin");
    const granted = await installationRepositories(token, installationId);
    const forks = new Set(granted.filter((r) => r.fork).slice(0, MAX_UPSTREAM_LOOKUPS).map((r) => r.id));
    repos = await Promise.all(
      granted.map(async (r) => ({ ...r, upstream: forks.has(r.id) ? await forkUpstream(token, r.full_name).catch(() => null) : null })),
    );
  } catch (e) {
    console.error("[github] installation lookup failed", e);
    return back("github-failed");
  }

  try {
    await withTenant(v.tenantId, async (db) => {
      await db.query(
        `INSERT INTO installations (id, tenant_id, account_login, account_type, repository_selection, suspended_at)
         VALUES ($1, $2, $3, $4, $5, $6)
         ON CONFLICT (id) DO UPDATE SET account_login = EXCLUDED.account_login, account_type = EXCLUDED.account_type,
           repository_selection = EXCLUDED.repository_selection, suspended_at = EXCLUDED.suspended_at, updated_at = now()`,
        [
          installationId,
          v.tenantId,
          installation.account?.login ?? "unknown",
          installation.account?.type ?? "unknown",
          installation.repository_selection,
          installation.suspended_at,
        ],
      );
      for (const r of repos) {
        await db.query(
          `INSERT INTO repositories (id, installation_id, tenant_id, full_name, default_branch, upstream)
           VALUES ($1, $2, $3, $4, $5, $6)
           ON CONFLICT (id) DO UPDATE SET installation_id = EXCLUDED.installation_id, full_name = EXCLUDED.full_name,
             default_branch = EXCLUDED.default_branch, upstream = coalesce(repositories.upstream, EXCLUDED.upstream), updated_at = now()`,
          [r.id, installationId, v.tenantId, r.full_name, r.default_branch ?? null, r.upstream],
        );
      }
      // Repositories the installation no longer grants.
      await db.query("DELETE FROM repositories WHERE installation_id = $1 AND NOT (id = ANY($2::bigint[]))", [installationId, repos.map((r) => r.id)]);
      await db.query("INSERT INTO audit_events (tenant_id, actor, action, subject, detail) VALUES ($1, $2, $3, $4, $5)", [
        v.tenantId,
        v.user.id,
        `github.installation.${action === "update" ? "updated" : "installed"}`,
        String(installationId),
        JSON.stringify({ account: installation.account?.login ?? null, repositories: repos.length }),
      ]);
    });
  } catch (e) {
    // Row-level security refuses to update an installation another tenant already recorded, and the
    // unique repository name refuses a repository another tenant already recorded.
    const code = (e as { code?: string }).code;
    if (code === "42501" || code === "23505") return back("claimed");
    throw e;
  }
  return back("installed");
}
