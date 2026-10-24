import { NextResponse } from "next/server";
import { z } from "zod";
import { UPSTREAM_RE } from "@/lib/jobs";
import { sameOrigin, viewer } from "@/lib/server/auth";
import { updateRepo } from "@/lib/server/jobs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const Body = z.object({
  upstream: z.string().regex(UPSTREAM_RE, "Use owner/name@branch, for example torvalds/linux@master.").nullable(),
  schedule: z.enum(["hourly", "daily", "weekly"]).nullable(),
});

/** Sets the upstream a fork follows and how often it syncs on its own. */
export async function PATCH(req: Request, ctx: { params: Promise<{ id: string }> }) {
  if (!sameOrigin(req)) return NextResponse.json({ error: "Bad request." }, { status: 403 });
  const v = await viewer(req.headers);
  if (!v) return NextResponse.json({ error: "Sign in first." }, { status: 401 });
  if (v.role !== "owner") return NextResponse.json({ error: "Only the account owner can change repository settings." }, { status: 403 });
  const { id } = await ctx.params;
  if (!/^\d{1,20}$/.test(id)) return NextResponse.json({ error: "No such repository." }, { status: 404 });
  const parsed = Body.safeParse(await req.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ error: parsed.error.issues[0]?.message ?? "Invalid settings." }, { status: 400 });
  const ok = await updateRepo(v.tenantId, id, parsed.data.upstream, parsed.data.schedule);
  return ok ? NextResponse.json({ ok: true }) : NextResponse.json({ error: "No such repository." }, { status: 404 });
}
