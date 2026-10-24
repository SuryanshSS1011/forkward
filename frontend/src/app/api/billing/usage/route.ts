import { NextResponse } from "next/server";
import { PLANS, amountDue, isPlanId } from "@/lib/plans";
import { viewer } from "@/lib/server/auth";
import { withTenant } from "@/lib/server/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** This month's usage for the signed-in user's tenant, from usage_quota and usage_events, computed
 * the same way as billing.usage() in Python. */
export async function GET(req: Request) {
  const v = await viewer(req.headers);
  if (!v) return NextResponse.json({ error: "Sign in first." }, { status: 401 });
  const out = await withTenant(v.tenantId, async (db) => {
    const month = (await db.query<{ m: string }>("SELECT to_char(date_trunc('month', now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD') AS m")).rows[0].m;
    const tenant = (await db.query<{ plan: string; stripe_customer_id: string | null }>("SELECT plan, stripe_customer_id FROM tenants WHERE id = $1", [v.tenantId])).rows[0];
    const byTool = (
      await db.query<{ tool: string; units: string; calls: string }>(
        "SELECT tool, sum(units) AS units, count(*) AS calls FROM usage_events WHERE tenant_id = $1 AND ts >= $2::date GROUP BY tool ORDER BY tool",
        [v.tenantId, month],
      )
    ).rows.map((r) => ({ tool: r.tool, units: Number(r.units), calls: Number(r.calls) }));
    const reserved = (await db.query<{ used: number }>("SELECT used FROM usage_quota WHERE tenant_id = $1 AND month = $2::date", [v.tenantId, month])).rows[0];
    return { month, tenant, byTool, reserved: reserved?.used ?? null };
  });
  const plan = isPlanId(out.tenant?.plan) ? out.tenant.plan : "trial";
  const used = out.byTool.reduce((s, r) => s + r.units, 0);
  const limit = PLANS[plan].monthly_units;
  return NextResponse.json({
    plan,
    role: v.role,
    hasBilling: Boolean(out.tenant?.stripe_customer_id),
    periodStart: out.month,
    unitsUsed: used,
    // usage_quota also counts units reserved by syncs still running.
    unitsReserved: out.reserved ?? used,
    unitsIncluded: PLANS[plan].included_units,
    unitsLimit: limit,
    unitsLeft: Math.max(0, limit - Math.max(used, out.reserved ?? 0)),
    amountDueUsd: amountDue(plan, used),
    byTool: out.byTool,
  });
}
