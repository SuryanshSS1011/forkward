import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { PLANS, amountDue, planDisplay } from "../plans";

/** PLANS from forkward/billing.py. The dict literal is JSON once trailing commas are dropped. */
function pythonPlans(): Record<string, Record<string, number>> {
  const src = readFileSync(resolve(__dirname, "../../../../forkward/billing.py"), "utf8");
  const m = src.match(/^PLANS: dict\[str, dict\] = (\{[\s\S]*?\n\})/m);
  if (!m) throw new Error("PLANS not found in forkward/billing.py");
  return JSON.parse(m[1].replace(/,(\s*[}\]])/g, "$1"));
}

describe("plans", () => {
  it("match PLANS in forkward/billing.py exactly", () => {
    expect(PLANS).toEqual(pythonPlans());
  });

  it("compute the amount due like billing.amount_due", () => {
    expect(amountDue("trial", 50)).toBe(0);
    expect(amountDue("team", 1999)).toBe(99);
    expect(amountDue("team", 2100)).toBe(104);
    expect(amountDue("enterprise", 50001)).toBe(1499.03);
  });

  it("describe each plan from the shared numbers", () => {
    expect(planDisplay("team").overage).toBe("Extra units $5 per 100, up to 5,000 a month");
    expect(planDisplay("enterprise").price).toBe("$1,499");
    expect(planDisplay("trial").units).toBe("50 units a month");
  });
});
