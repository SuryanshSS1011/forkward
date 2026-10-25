import { planPrices, stripeConfigured } from "@/lib/server/stripe";
import BillingClient from "./BillingClient";

// Whether paid plans can be bought depends on this server's Stripe settings, read at request time.
export const dynamic = "force-dynamic";
export const metadata = { title: "Pricing and usage | Forkward" };

export default function Billing() {
  const checkoutReady = stripeConfigured() && planPrices("team") !== null && planPrices("enterprise") !== null;
  return <BillingClient checkoutReady={checkoutReady} />;
}
