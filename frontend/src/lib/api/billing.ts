import type { PaidPlanId, PlanId } from "@/lib/plans";

export type Usage = {
  plan: PlanId;
  role: "owner" | "member";
  hasBilling: boolean;
  periodStart: string;
  unitsUsed: number;
  unitsReserved: number;
  unitsIncluded: number;
  unitsLimit: number;
  unitsLeft: number;
  amountDueUsd: number;
  byTool: { tool: string; units: number; calls: number }[];
};

async function post(path: string, body: unknown = {}): Promise<string> {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const b = await res.json().catch(() => ({}));
  if (!res.ok || !b.url) throw new Error(b.error ?? "Billing is not available right now.");
  return b.url as string;
}

/** The signed-in tenant's usage this month, or null when signed out. */
export async function usage(): Promise<Usage | null> {
  const res = await fetch("/api/billing/usage", { cache: "no-store" });
  if (res.status === 401) return null;
  if (!res.ok) throw new Error("Could not load your usage.");
  return (await res.json()) as Usage;
}

/** Stripe Checkout for a paid plan, or the billing portal when the tenant already subscribes. */
export const checkout = (plan: PaidPlanId) => post("/api/billing/checkout", { plan });
export const portal = () => post("/api/billing/portal");
