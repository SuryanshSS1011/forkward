import { NextResponse } from "next/server";
import { z } from "zod";
import { sameOrigin, viewer } from "@/lib/server/auth";
import { reviewHunk } from "@/lib/server/jobs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const Body = z.object({
  decision: z.enum(["accepted", "rejected"]).nullable(),
  resolution: z.string().max(200_000).optional(),
});

/** Accept (optionally with an edit), reject, or clear the decision on one hunk. */
export async function POST(req: Request, ctx: { params: Promise<{ id: string; hunk: string }> }) {
  if (!sameOrigin(req)) return NextResponse.json({ error: "Bad request." }, { status: 403 });
  const v = await viewer(req.headers);
  if (!v) return NextResponse.json({ error: "Sign in first." }, { status: 401 });
  const { id, hunk } = await ctx.params;
  if (!UUID.test(id) || !UUID.test(hunk)) return NextResponse.json({ error: "No such hunk." }, { status: 404 });
  const parsed = Body.safeParse(await req.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ error: "Invalid review." }, { status: 400 });
  const r = await reviewHunk(v.tenantId, id, hunk, parsed.data, v.user.id);
  return "ok" in r ? NextResponse.json(r) : NextResponse.json({ error: r.error }, { status: r.status });
}
