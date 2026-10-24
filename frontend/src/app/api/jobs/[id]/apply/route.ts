import { NextResponse } from "next/server";
import { sameOrigin, viewer } from "@/lib/server/auth";
import { requestApply } from "@/lib/server/jobs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Commits every accepted hunk to the sync branch, through the worker. */
export async function POST(req: Request, ctx: { params: Promise<{ id: string }> }) {
  if (!sameOrigin(req)) return NextResponse.json({ error: "Bad request." }, { status: 403 });
  const v = await viewer(req.headers);
  if (!v) return NextResponse.json({ error: "Sign in first." }, { status: 401 });
  if (v.role !== "owner") return NextResponse.json({ error: "Only the account owner can push reviewed resolutions." }, { status: 403 });
  const { id } = await ctx.params;
  if (!UUID.test(id)) return NextResponse.json({ error: "No such job." }, { status: 404 });
  const r = await requestApply(v.tenantId, id, v.user.id);
  return "ok" in r ? NextResponse.json(r, { status: 202 }) : NextResponse.json({ error: r.error }, { status: r.status });
}
