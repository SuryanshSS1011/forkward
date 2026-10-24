"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useMe } from "@/components/useMe";
import { type Usage, checkout, portal, usage as loadUsage } from "@/lib/api/billing";
import { PAID_PLANS, PLAN_COPY, isPaidPlan, planDisplay, type PlanId } from "@/lib/plans";

const plans = (["trial", ...PAID_PLANS] as PlanId[]).map(planDisplay);
const n = (x: number) => x.toLocaleString("en-US");

const TOOL_NAMES: Record<string, string> = { open_sync_pr: "Syncs", preview: "Previews", resolve_conflicts: "Resolve calls" };

export default function Billing() {
  const [u, setU] = useState<Usage | null | undefined>(undefined);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const [me] = useMe();
  useEffect(() => {
    if (me === undefined) return;
    if (!me) return setU(null);
    loadUsage()
      .then(setU)
      .catch(() => setU(null));
  }, [me]);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    if (q.get("checkout") === "success") setNote("Thanks. Your plan changes as soon as Stripe confirms the subscription, usually within a minute.");
    else if (q.get("checkout") === "cancelled") setNote("Checkout was cancelled. Nothing changed.");
  }, []);

  const go = async (key: string, fn: () => Promise<string>) => {
    setBusy(key);
    setNote(null);
    try {
      window.location.assign(await fn());
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Billing is not available right now.");
      setBusy(null);
    }
  };

  const signedIn = Boolean(u);
  const owner = u?.role === "owner";
  return (
    <div className="wrap pb-6 pt-12 md:pt-16">
      <h1 className="t-h1 max-w-[20ch]">{me ? "Usage and plan" : "Pay per conflict"}</h1>
      <p className="t-lead mt-3 max-w-[58ch]">A unit is one conflict Forkward proposes a resolution for on the default model, applied or sent to you. Previews use units too. Failed runs are free.</p>

      {u && (
        <section aria-label="Your usage" className="mt-8 max-w-2xl rounded-btn border border-line bg-raised p-4">
          <p className="t-label text-muted">This month, since {u.periodStart}</p>
          <p className="t-stat mt-1">
            {n(u.unitsUsed)}
            <span className="text-[15px] font-normal text-muted"> of {n(u.unitsLimit)} units on {PLAN_COPY[u.plan].name}</span>
          </p>
          <div className="mt-3 h-2 overflow-hidden rounded-full bg-sunken" aria-hidden>
            <div className="h-full bg-honey" style={{ width: `${Math.min(100, (u.unitsReserved / Math.max(1, u.unitsLimit)) * 100)}%` }} />
          </div>
          <p className="t-label mt-2 text-muted">
            {n(u.unitsLeft)} units left. {n(u.unitsIncluded)} included in the plan.
            {u.unitsReserved > u.unitsUsed && <> {n(u.unitsReserved - u.unitsUsed)} reserved by syncs still running.</>}
            {u.amountDueUsd > 0 && <> Estimated bill ${u.amountDueUsd.toFixed(2)}.</>}
          </p>
          {u.byTool.length > 0 && (
            <ul className="t-label mt-3 text-muted">
              {u.byTool.map((t) => (
                <li key={t.tool}>
                  {TOOL_NAMES[t.tool] ?? t.tool}: {n(t.units)} units in {n(t.calls)} {t.calls === 1 ? "run" : "runs"}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      <ul className="mt-8 grid border-t border-line md:grid-cols-3 md:[&>li+li]:border-l md:[&>li+li]:pl-8 md:[&>li:not(:last-child)]:pr-8 [&>li]:border-line max-md:[&>li+li]:border-t">
        {plans.map((p) => (
          <li key={p.id} className="flex flex-col py-6">
            <h2 className="text-[18px] font-semibold">{p.name}</h2>
            <p className="t-stat mt-2">{p.price}<span className="text-[15px] font-normal text-muted">{p.unit}</span></p>
            <p className="mt-2 text-[16px] font-semibold text-ink">{p.units}</p>
            <p className="t-label text-muted">{p.overage}</p>
            <p className="mt-3 flex-1 text-muted">{p.blurb}</p>
            {u?.plan === p.id ? (
              <span className="chip mt-5 self-start">Your plan</span>
            ) : !signedIn ? (
              <Link href="/signin?next=/billing" className="btn btn-accent btn-sm mt-5 self-start">{p.cta}</Link>
            ) : isPaidPlan(p.id) && owner ? (
              <button className="btn btn-accent btn-sm mt-5 self-start" disabled={busy !== null} onClick={() => go(p.id, () => checkout(p.id as "team" | "enterprise"))}>
                {busy === p.id ? "Opening…" : p.cta}
              </button>
            ) : null}
          </li>
        ))}
      </ul>
      {u?.hasBilling && owner && (
        <p className="mt-2">
          <button className="btn btn-outline-teal btn-sm" disabled={busy !== null} onClick={() => go("portal", portal)}>
            {busy === "portal" ? "Opening…" : "Manage subscription, usage and invoices"}
          </button>
        </p>
      )}
      {note && <p role="status" className="mt-3 text-muted">{note}</p>}
    </div>
  );
}
