import { NextResponse } from "next/server";
import { viewer } from "@/lib/server/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** The signed-in user and their tenant, or {user: null} when signed out. Being signed out is a
 * normal state for this endpoint, so it answers 200 rather than filling the console with 401s. */
export async function GET(req: Request) {
  const v = await viewer(req.headers);
  if (!v) return NextResponse.json({ user: null }, { headers: { "Cache-Control": "no-store" } });
  return NextResponse.json({
    user: { id: v.user.id, name: v.user.name, email: v.user.email, image: v.user.image ?? null, tenantId: v.tenantId, role: v.role, plan: v.plan },
  });
}
