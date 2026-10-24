import { NextResponse } from "next/server";
import { viewer } from "@/lib/server/auth";
import { getJob } from "@/lib/server/jobs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** One job with its hunks and its events after ?after=<event id>, for polling. */
export async function GET(req: Request, ctx: { params: Promise<{ id: string }> }) {
  const v = await viewer(req.headers);
  if (!v) return NextResponse.json({ error: "Sign in first." }, { status: 401 });
  const { id } = await ctx.params;
  if (!UUID.test(id)) return NextResponse.json({ error: "No such job." }, { status: 404 });
  const after = Math.max(0, Number(new URL(req.url).searchParams.get("after") ?? 0) || 0);
  const job = await getJob(v.tenantId, id, after);
  return job ? NextResponse.json(job, { headers: { "Cache-Control": "no-store" } }) : NextResponse.json({ error: "No such job." }, { status: 404 });
}
