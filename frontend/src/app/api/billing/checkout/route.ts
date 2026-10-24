import { NextResponse } from "next/server";
import { isPaidPlan } from "@/lib/plans";
import { sameOrigin, viewer } from "@/lib/server/auth";
import { withTenant } from "@/lib/server/db";
import { appUrl } from "@/lib/server/env";
import { planPrices, stripe, stripeConfigured } from "@/lib/server/stripe";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Starts Stripe Checkout for the team or enterprise plan: the flat monthly price plus the metered
 * price on the usage meter. A tenant that already subscribes is sent to the billing portal instead.
 * Returns { url }. The plan changes only when the webhook confirms the subscription. */
export async function POST(req: Request) {
  if (!sameOrigin(req)) return NextResponse.json({ error: "Bad request." }, { status: 403 });
  const v = await viewer(req.headers);
  if (!v) return NextResponse.json({ error: "Sign in first." }, { status: 401 });
  if (v.role !== "owner") return NextResponse.json({ error: "Only the account owner can change the plan." }, { status: 403 });
  const { plan } = (await req.json().catch(() => ({}))) as { plan?: unknown };
  if (!isPaidPlan(plan)) return NextResponse.json({ error: "Choose the team or enterprise plan." }, { status: 400 });
  const prices = planPrices(plan);
  if (!stripeConfigured() || !prices) return NextResponse.json({ error: "Billing is not set up on this server yet." }, { status: 503 });

  const tenant = await withTenant(v.tenantId, async (db) => {
    const { rows } = await db.query<{ stripe_customer_id: string | null; stripe_subscription_id: string | null }>(
      "SELECT stripe_customer_id, stripe_subscription_id FROM tenants WHERE id = $1",
      [v.tenantId],
    );
    return rows[0];
  });
  const base = appUrl();
  if (tenant?.stripe_customer_id && tenant.stripe_subscription_id) {
    const portal = await stripe().billingPortal.sessions.create({ customer: tenant.stripe_customer_id, return_url: `${base}/billing` });
    return NextResponse.json({ url: portal.url });
  }
  const session = await stripe().checkout.sessions.create({
    mode: "subscription",
    line_items: [{ price: prices.base, quantity: 1 }, { price: prices.metered }],
    client_reference_id: v.tenantId,
    metadata: { tenant_id: v.tenantId, plan },
    subscription_data: { metadata: { tenant_id: v.tenantId, plan } },
    ...(tenant?.stripe_customer_id ? { customer: tenant.stripe_customer_id } : { customer_email: v.user.email }),
    success_url: `${base}/billing?checkout=success`,
    cancel_url: `${base}/billing?checkout=cancelled`,
  });
  if (!session.url) return NextResponse.json({ error: "Stripe did not return a checkout page." }, { status: 502 });
  return NextResponse.json({ url: session.url });
}
