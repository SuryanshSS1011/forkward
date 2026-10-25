"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { MarketingShell } from "@/components/MarketingShell";
import { useMe } from "@/components/useMe";
import { type Usage, checkout, portal, usage as loadUsage } from "@/lib/api/billing";
import { PAID_PLANS, PLAN_COPY, isPaidPlan, planDisplay, type PlanId } from "@/lib/plans";

const plans = (["trial", ...PAID_PLANS] as PlanId[]).map(planDisplay);
const n = (x: number) => x.toLocaleString("en-US");

const TOOL_NAMES: Record<string, string> = { open_sync_pr: "Syncs", preview: "Previews", resolve_conflicts: "Resolve calls" };

/** Shown on paid plans while this server cannot take payment, instead of a button that would fail. */
const NOT_ON_SALE = "Not on sale yet. The free plan has every feature.";

// Each plan card's hand-cut paper corners, by index.
const CARD = [
  { radius: "11px 5px 14px 6px", value: "text-[var(--pa-ink)]" },
  { radius: "8px 14px 6px 12px", value: "text-[var(--pa-primary)]" },
  { radius: "12px 6px 15px 5px", value: "text-[var(--pa-secondary)]" },
];

/** Public pricing when signed out, the workspace's usage and plan when signed in. Paid plans offer a
 * button only when `checkoutReady`, that is when this server has Stripe and both plans' prices. */
export default function BillingClient({ checkoutReady }: { checkoutReady: boolean }) {
  const [u, setU] = useState<Usage | null | undefined>(undefined);
  const [usageError, setUsageError] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const [me] = useMe();
  useEffect(() => {
    if (!me) return;
    loadUsage()
      .then((x) => {
        setU(x);
        setUsageError(false);
      })
      .catch(() => {
        setU(null);
        setUsageError(true);
      });
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

  // The session is still loading: neither view yet, so a signed-in user never sees the public page
  // flash first.
  if (me === undefined) {
    return (
      <div className="wrap pb-16 pt-12" aria-busy="true">
        <p className="text-muted">Loading…</p>
      </div>
    );
  }

  // Signed out: the public pricing page, in the parchment marketing skin.
  if (me === null) {
    return (
      <MarketingShell active="pricing">
        <section className="mx-auto w-full max-w-7xl px-6 pb-10 pt-12 lg:px-12 md:pt-16">
          <h1 className="max-w-[20ch] font-display text-[32px] font-extrabold tracking-tight text-[var(--pa-ink)] md:text-[44px]">Simple, pay as you go.</h1>
          <p className="mt-3 max-w-[58ch] text-[18px] leading-relaxed text-[var(--pa-ink-muted)]">
            One resolution is one unit, whether Forkward applies it or sends it to you. Previews count too, and anything that fails is free.
          </p>
          <div className="mt-10 grid gap-8 md:grid-cols-3">
            {plans.map((p, i) => {
              const c = CARD[i % CARD.length];
              const paid = isPaidPlan(p.id);
              return (
                <div
                  key={p.id}
                  className="pa-lift relative flex flex-col border-2 border-[var(--pa-outline)] bg-[var(--pa-surface)] p-6 shadow-[4px_4px_0px_#8a716e]"
                  style={{ borderRadius: c.radius }}
                >
                  <h2 className="font-display text-[24px] font-bold text-[var(--pa-ink)]">{p.name}</h2>
                  <p className="mt-2 flex items-baseline gap-1">
                    <span className={`font-display text-[40px] font-extrabold tracking-tight ${c.value}`}>{p.price}</span>
                    <span className="font-label text-[13px] text-[var(--pa-ink-muted)]">{p.unit}</span>
                  </p>
                  <p className="mt-2 font-semibold text-[var(--pa-ink)]">{p.units}</p>
                  <p className="font-label text-[11px] text-[var(--pa-ink-muted)]">{p.overage}</p>
                  <p className="mt-3 flex-1 leading-relaxed text-[var(--pa-ink-muted)]">{p.blurb}</p>
                  {paid && !checkoutReady ? (
                    <p className="mt-6 font-label text-[13px] text-[var(--pa-ink-muted)]">{NOT_ON_SALE}</p>
                  ) : (
                    <Link
                      href={paid ? "/signin?next=/billing" : "/signin?next=/repos"}
                      className="mt-6 self-start border-2 border-[var(--pa-charcoal)] bg-[var(--pa-primary-container)] px-6 py-2 font-label text-[14px] font-bold text-[var(--pa-on-primary)] shadow-[4px_4px_0px_#33302a] transition-all hover:-translate-y-0.5 active:translate-x-[2px] active:translate-y-[2px] active:shadow-[1px_1px_0px_#33302a]"
                      style={{ borderRadius: "8px 16px 6px 14px / 14px 6px 13px 7px" }}
                    >
                      {p.cta}
                    </Link>
                  )}
                </div>
              );
            })}
          </div>
          {note && (
            <p role="status" className="mt-6 text-[var(--pa-ink-muted)]">
              {note}
            </p>
          )}
        </section>
      </MarketingShell>
    );
  }

  // Signed in: the Usage and plan workspace page, in the app's own style.
  const owner = me.role === "owner";
  return (
    <div className="wrap pb-6 pt-12 md:pt-16">
      <h1 className="t-h1 max-w-[20ch]">Usage and plan</h1>
      <p className="t-lead mt-3 max-w-[58ch]">
        A unit is one conflict Forkward proposes a resolution for on the default model, applied or sent to you. Previews use units too. Failed runs are free.
      </p>

      {u === undefined && !usageError && <p className="mt-8 text-muted">Loading your usage…</p>}
      {usageError && (
        <p role="alert" className="mt-8 text-danger">
          Could not load your usage. Reload the page to try again.
        </p>
      )}
      {u && (
        <section aria-label="Your usage" className="mt-8 max-w-2xl rounded-btn border border-line bg-raised p-4">
          <p className="t-label text-muted">This month, since {u.periodStart}</p>
          <p className="t-stat mt-1">
            {n(u.unitsUsed)}
            <span className="text-[15px] font-normal text-muted">
              {" "}
              of {n(u.unitsLimit)} units on {PLAN_COPY[u.plan].name}
            </span>
          </p>
          <div
            className="mt-3 h-2 overflow-hidden rounded-full bg-sunken"
            role="progressbar"
            aria-label="Units used this month"
            aria-valuemin={0}
            aria-valuemax={u.unitsLimit}
            aria-valuenow={u.unitsReserved}
          >
            <div className="h-full bg-honey" style={{ width: `${Math.min(100, (u.unitsReserved / Math.max(1, u.unitsLimit)) * 100)}%` }} />
          </div>
          <p className="t-label mt-2 text-muted">
            {n(u.unitsLeft)} units left. {n(u.unitsIncluded)} included in the plan.
            {u.unitsReserved > u.unitsUsed && <> {n(u.unitsReserved - u.unitsUsed)} reserved by syncs still running.</>}
            {u.amountDueUsd > 0 && <> Estimated bill ${u.amountDueUsd.toFixed(2)}.</>}
          </p>
          {u.byTool.length > 0 ? (
            <ul className="t-label mt-3 text-muted">
              {u.byTool.map((t) => (
                <li key={t.tool}>
                  {TOOL_NAMES[t.tool] ?? t.tool}: {n(t.units)} units in {n(t.calls)} {t.calls === 1 ? "run" : "runs"}
                </li>
              ))}
            </ul>
          ) : (
            <p className="t-label mt-3 text-muted">
              Nothing used yet this month.{" "}
              <Link href="/repos" className="link">
                Start a preview
              </Link>
            </p>
          )}
        </section>
      )}

      <ul className="mt-8 grid border-t border-line md:grid-cols-3 md:[&>li+li]:border-l md:[&>li+li]:pl-8 md:[&>li:not(:last-child)]:pr-8 [&>li]:border-line max-md:[&>li+li]:border-t">
        {plans.map((p) => (
          <li key={p.id} className="flex flex-col py-6">
            <h2 className="text-[18px] font-semibold">{p.name}</h2>
            <p className="t-stat mt-2">
              {p.price}
              <span className="text-[15px] font-normal text-muted">{p.unit}</span>
            </p>
            <p className="mt-2 text-[16px] font-semibold text-ink">{p.units}</p>
            <p className="t-label text-muted">{p.overage}</p>
            <p className="mt-3 flex-1 text-muted">{p.blurb}</p>
            {u?.plan === p.id ? (
              <span className="chip mt-5 self-start">Your plan</span>
            ) : isPaidPlan(p.id) && !checkoutReady ? (
              <p className="t-label mt-5 text-muted">{NOT_ON_SALE}</p>
            ) : isPaidPlan(p.id) && owner ? (
              <button
                className="btn btn-accent btn-sm mt-5 self-start"
                disabled={busy !== null}
                onClick={() => go(p.id, () => checkout(p.id as "team" | "enterprise"))}
              >
                {busy === p.id ? "Opening…" : p.cta}
              </button>
            ) : isPaidPlan(p.id) ? (
              <p className="t-label mt-5 text-muted">Only the workspace owner can change the plan.</p>
            ) : null}
          </li>
        ))}
      </ul>
      {u?.hasBilling && owner && checkoutReady && (
        <p className="mt-2">
          <button className="btn btn-outline-teal btn-sm" disabled={busy !== null} onClick={() => go("portal", portal)}>
            {busy === "portal" ? "Opening…" : "Manage subscription, usage and invoices"}
          </button>
        </p>
      )}
      {note && (
        <p role="status" className="mt-3 text-muted">
          {note}
        </p>
      )}
    </div>
  );
}
