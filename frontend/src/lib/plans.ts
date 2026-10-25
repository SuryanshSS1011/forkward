/**
 * The plans, mirroring PLANS in forkward/billing.py field for field. A unit is one conflict hunk
 * resolved on the default model. A monthly fee covers `included_units`, each unit past that costs
 * `price_per_unit_usd`, and calls stop at `monthly_units`. src/lib/__tests__/plans.test.ts fails if
 * these numbers drift from Python, and the Stripe prices must match them too.
 */
export const PLANS = {
  trial: { monthly_units: 500, included_units: 500, fee_usd: 0, price_per_unit_usd: 0 },
  team: { monthly_units: 5000, included_units: 2000, fee_usd: 99, price_per_unit_usd: 0.05 },
  enterprise: { monthly_units: 100000, included_units: 50000, fee_usd: 1499, price_per_unit_usd: 0.03 },
} as const;

export type PlanId = keyof typeof PLANS;
export type PaidPlanId = Exclude<PlanId, "trial">;
export const PAID_PLANS: readonly PaidPlanId[] = ["team", "enterprise"];

export const isPlanId = (p: unknown): p is PlanId => typeof p === "string" && p in PLANS;
export const isPaidPlan = (p: unknown): p is PaidPlanId => p === "team" || p === "enterprise";

const n = (x: number) => x.toLocaleString("en-US");
const usd = (x: number) => `$${x.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;

/** What the pricing page shows for each plan, derived from the numbers above. */
export const PLAN_COPY: Record<PlanId, { name: string; blurb: string; cta: string }> = {
  trial: { name: "Starter", blurb: "Try Forkward on one repository. No card needed.", cta: "Start free" },
  team: { name: "Team", blurb: "For teams keeping a few repositories in step with upstream.", cta: "Choose Team" },
  enterprise: { name: "Enterprise", blurb: "For companies with many long-lived repositories.", cta: "Choose Enterprise" },
};

export function planDisplay(id: PlanId) {
  const p = PLANS[id];
  const paid = p.price_per_unit_usd > 0;
  return {
    id,
    ...PLAN_COPY[id],
    price: usd(p.fee_usd),
    unit: "/month",
    units: paid ? `${n(p.included_units)} units a month included` : `${n(p.monthly_units)} units a month`,
    overage: paid
      ? `Extra units ${usd(p.price_per_unit_usd * 100)} per 100, up to ${n(p.monthly_units)} a month`
      : `Stops at ${n(p.monthly_units)} units`,
  };
}

/** The monthly fee plus overage past the included units, as billing.amount_due computes it. */
export function amountDue(plan: PlanId, used: number): number {
  const p = PLANS[plan];
  return Math.round((p.fee_usd + Math.max(0, used - p.included_units) * p.price_per_unit_usd) * 100) / 100;
}
