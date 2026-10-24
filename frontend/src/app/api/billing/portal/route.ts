import { NextResponse } from "next/server";
import { sameOrigin, viewer } from "@/lib/server/auth";
import { withTenant } from "@/lib/server/db";
import { appUrl } from "@/lib/server/env";
import { stripe, stripeConfigured } from "@/lib/server/stripe";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Opens the Stripe billing portal for the tenant's customer. Returns { url }. */
export async function POST(req: Request) {
  if (!sameOrigin(req)) return NextResponse.json({ error: "Bad request." }, { status: 403 });
  const v = await viewer(req.headers);
  if (!v) return NextResponse.json({ error: "Sign in first." }, { status: 401 });
  if (v.role !== "owner") return NextResponse.json({ error: "Only the account owner can manage billing." }, { status: 403 });
  if (!stripeConfigured()) return NextResponse.json({ error: "Billing is not set up on this server yet." }, { status: 503 });
  const customer = await withTenant(v.tenantId, async (db) => {
    const { rows } = await db.query<{ stripe_customer_id: string | null }>("SELECT stripe_customer_id FROM tenants WHERE id = $1", [v.tenantId]);
    return rows[0]?.stripe_customer_id ?? null;
  });
  if (!customer) return NextResponse.json({ error: "Choose a paid plan first." }, { status: 409 });
  const portal = await stripe().billingPortal.sessions.create({ customer, return_url: `${appUrl()}/billing` });
  return NextResponse.json({ url: portal.url });
}
