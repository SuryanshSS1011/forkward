import Stripe from "stripe";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { POST } from "@/app/api/stripe/webhook/route";

const SECRET = "whsec_test_secret";
const payload = JSON.stringify({ id: "evt_test", object: "event", type: "invoice.created", data: { object: {} } });

const post = (body: string, signature?: string) =>
  POST(
    new Request("http://localhost/api/stripe/webhook", {
      method: "POST",
      body,
      headers: signature ? { "stripe-signature": signature } : {},
    }),
  );

beforeEach(() => {
  process.env.STRIPE_WEBHOOK_SECRET = SECRET;
});
afterEach(() => {
  delete process.env.STRIPE_WEBHOOK_SECRET;
});

describe("Stripe webhook", () => {
  it("rejects a request without a signature", async () => {
    expect((await post(payload)).status).toBe(400);
  });

  it("rejects a signature made with another secret", async () => {
    const forged = Stripe.webhooks.generateTestHeaderString({ payload, secret: "whsec_attacker" });
    expect((await post(payload, forged)).status).toBe(400);
  });

  it("rejects a body changed after signing", async () => {
    const sig = Stripe.webhooks.generateTestHeaderString({ payload, secret: SECRET });
    expect((await post(payload.replace("invoice.created", "checkout.session.completed"), sig)).status).toBe(400);
  });

  it("accepts a correctly signed event", async () => {
    const sig = Stripe.webhooks.generateTestHeaderString({ payload, secret: SECRET });
    expect((await post(payload, sig)).status).toBe(200);
  });
});
