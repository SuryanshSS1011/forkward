import { NextResponse } from "next/server";
import Stripe from "stripe";
import { serverEnv } from "@/lib/server/env";
import { applyStripeEvent } from "@/lib/server/stripe";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Stripe's webhook. The signature is checked against the raw body with STRIPE_WEBHOOK_SECRET before
 * anything is parsed or written. Handles checkout.session.completed and customer.subscription.*. */
export async function POST(req: Request) {
  const secret = serverEnv("STRIPE_WEBHOOK_SECRET");
  const signature = req.headers.get("stripe-signature");
  if (!secret) return NextResponse.json({ error: "Webhook secret is not configured." }, { status: 503 });
  if (!signature) return NextResponse.json({ error: "Missing signature." }, { status: 400 });
  const body = await req.text();
  let event: Stripe.Event;
  try {
    event = Stripe.webhooks.constructEvent(body, signature, secret);
  } catch {
    return NextResponse.json({ error: "Invalid signature." }, { status: 400 });
  }
  const result = await applyStripeEvent(event);
  console.log(`[stripe] ${event.type} ${event.id}: ${result}`);
  return NextResponse.json({ received: true });
}
