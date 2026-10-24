import Stripe from "stripe";
import type { PoolClient } from "pg";
import { isPaidPlan, type PaidPlanId, type PlanId } from "@/lib/plans";
import { validTenantId, withSystem } from "./db";
import { serverEnv } from "./env";

let client: Stripe | null = null;

/** Created on first use, because `next build` has no STRIPE_SECRET_KEY. */
export function stripe(): Stripe {
  if (!client) {
    const key = serverEnv("STRIPE_SECRET_KEY");
    if (!key) throw new Error("STRIPE_SECRET_KEY is not set");
    client = new Stripe(key);
  }
  return client;
}

export function stripeConfigured(): boolean {
  return Boolean(serverEnv("STRIPE_SECRET_KEY"));
}

/** Each paid plan is a flat monthly price plus a metered price on the STRIPE_METER_EVENT meter. */
export function planPrices(plan: PaidPlanId): { base: string; metered: string } | null {
  const prefix = plan === "team" ? "STRIPE_PRICE_TEAM" : "STRIPE_PRICE_ENTERPRISE";
  const base = serverEnv(`${prefix}_BASE`);
  const metered = serverEnv(`${prefix}_METERED`);
  return base && metered ? { base, metered } : null;
}

/** The plan a subscription's prices belong to, or null when they match no configured plan. */
export function planForPrices(priceIds: string[]): PaidPlanId | null {
  for (const plan of ["enterprise", "team"] as const) {
    const p = planPrices(plan);
    if (p && (priceIds.includes(p.base) || priceIds.includes(p.metered))) return plan;
  }
  return null;
}

const LIVE_STATUSES = new Set(["active", "trialing", "past_due"]);
const id = (x: string | { id: string } | null | undefined) => (typeof x === "string" ? x : (x?.id ?? null));

async function audit(db: PoolClient, tenantId: string, event: Stripe.Event, detail: Record<string, unknown>) {
  await db.query("INSERT INTO audit_events (tenant_id, actor, action, subject, detail) VALUES ($1, 'stripe', $2, $3, $4)", [
    tenantId,
    `billing.${event.type}`,
    event.id,
    JSON.stringify(detail),
  ]);
}

/**
 * Two Checkout sessions that both complete leave a tenant with two live subscriptions, and the one
 * not recorded would keep charging the base fee. Keeps the older and cancels the newer, prorated
 * and invoiced now so the customer is credited for it. Returns the subscription id to keep.
 */
async function keepOneLive(current: string | null, incoming: Stripe.Subscription): Promise<string> {
  if (!current || current === incoming.id || !LIVE_STATUSES.has(incoming.status)) return incoming.id;
  let existing: Stripe.Subscription;
  try {
    existing = await stripe().subscriptions.retrieve(current);
  } catch {
    return incoming.id;
  }
  if (!LIVE_STATUSES.has(existing.status)) return incoming.id;
  const [keep, drop] = existing.created <= incoming.created ? [existing, incoming] : [incoming, existing];
  await stripe().subscriptions.cancel(drop.id, { prorate: true, invoice_now: true });
  return keep.id;
}

async function tenantForCustomer(db: PoolClient, customer: string | null): Promise<string | null> {
  if (!customer) return null;
  const { rows } = await db.query<{ id: string }>("SELECT id FROM tenants WHERE stripe_customer_id = $1", [customer]);
  return rows[0]?.id ?? null;
}

/**
 * Writes a verified Stripe event to `tenants` with the system role, plus an audit row. Every write
 * sets absolute values, so a redelivered event changes nothing. Returns what it did, for logging.
 */
export async function applyStripeEvent(event: Stripe.Event): Promise<string> {
  if (event.type === "checkout.session.completed") {
    const s = event.data.object;
    const tenantId = s.client_reference_id ?? s.metadata?.tenant_id ?? null;
    const plan = s.metadata?.plan;
    if (s.mode !== "subscription" || !validTenantId(tenantId) || !isPaidPlan(plan)) return "ignored";
    const customer = id(s.customer);
    const subscription = id(s.subscription);
    return withSystem(async (db) => {
      const { rows } = await db.query<{ stripe_subscription_id: string | null }>("SELECT stripe_subscription_id FROM tenants WHERE id = $1", [tenantId]);
      if (!rows[0]) return "unknown tenant";
      if (subscription) {
        const incoming = await stripe().subscriptions.retrieve(subscription);
        // Already cancelled, for example as a duplicate when its subscription event came first.
        if (!LIVE_STATUSES.has(incoming.status)) return "ignored ended subscription";
        const kept = await keepOneLive(rows[0].stripe_subscription_id, incoming);
        if (kept !== subscription) {
          await audit(db, tenantId, event, { duplicate: subscription, kept });
          return `tenant ${tenantId} already subscribed; cancelled duplicate ${subscription}`;
        }
      }
      await db.query(
        "UPDATE tenants SET plan = $2, stripe_customer_id = $3, stripe_subscription_id = $4, updated_at = now() WHERE id = $1",
        [tenantId, plan, customer, subscription],
      );
      await audit(db, tenantId, event, { plan, customer, subscription });
      return `tenant ${tenantId} on ${plan}`;
    });
  }

  if (
    event.type === "customer.subscription.created" ||
    event.type === "customer.subscription.updated" ||
    event.type === "customer.subscription.deleted"
  ) {
    // Events can arrive out of order, so the subscription is read back from Stripe: an older
    // event delivered late then applies the current state instead of rolling the plan back.
    const sub = await stripe().subscriptions.retrieve(event.data.object.id);
    const customer = id(sub.customer);
    const live = event.type !== "customer.subscription.deleted" && LIVE_STATUSES.has(sub.status);
    const paid = planForPrices(sub.items.data.map((i) => i.price.id));
    return withSystem(async (db) => {
      const fromMeta = sub.metadata?.tenant_id;
      const tenantId = validTenantId(fromMeta) ? fromMeta : await tenantForCustomer(db, customer);
      if (!tenantId) return "unknown tenant";
      const { rows } = await db.query<{ stripe_subscription_id: string | null }>("SELECT stripe_subscription_id FROM tenants WHERE id = $1", [tenantId]);
      if (!rows[0]) return "unknown tenant";
      const current = rows[0].stripe_subscription_id;
      // A live subscription becomes the tenant's current one. An ended subscription only changes the
      // plan if it is the current one, so a late event for an old subscription cannot downgrade.
      if (!live && current && current !== sub.id) return "ignored stale subscription";
      if (live && (await keepOneLive(current, sub)) !== sub.id) {
        await audit(db, tenantId, event, { duplicate: sub.id, kept: current });
        return `tenant ${tenantId} already subscribed; cancelled duplicate ${sub.id}`;
      }
      const plan: PlanId = live && paid ? paid : "trial";
      await db.query(
        "UPDATE tenants SET plan = $2, stripe_customer_id = coalesce($3, stripe_customer_id), stripe_subscription_id = $4, updated_at = now() WHERE id = $1",
        [tenantId, plan, customer, live ? sub.id : null],
      );
      await audit(db, tenantId, event, { plan, status: sub.status, customer, subscription: sub.id });
      return `tenant ${tenantId} on ${plan}`;
    });
  }
  return "ignored";
}
