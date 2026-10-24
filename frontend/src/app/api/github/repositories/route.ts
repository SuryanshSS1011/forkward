import { NextResponse } from "next/server";
import { viewer } from "@/lib/server/auth";
import { withTenant } from "@/lib/server/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** The repositories the tenant's forkward-bot installations grant, as recorded by /api/github/installed. */
export async function GET(req: Request) {
  const v = await viewer(req.headers);
  if (!v) return NextResponse.json({ error: "Sign in first." }, { status: 401 });
  const repositories = await withTenant(v.tenantId, async (db) => {
    const { rows } = await db.query<{ full_name: string; upstream: string | null; account_login: string }>(
      "SELECT r.full_name, r.upstream, i.account_login FROM repositories r JOIN installations i ON i.id = r.installation_id " +
        "WHERE r.tenant_id = $1 ORDER BY lower(r.full_name)",
      [v.tenantId],
    );
    return rows;
  });
  return NextResponse.json({ repositories });
}
