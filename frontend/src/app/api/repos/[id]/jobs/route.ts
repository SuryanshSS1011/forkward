import { NextResponse } from "next/server";
import { z } from "zod";
import { sameOrigin, viewer } from "@/lib/server/auth";
import { startJob } from "@/lib/server/jobs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const Body = z.object({ kind: z.enum(["preview", "sync"]) });

/** Starts a preview (nothing is pushed) or a sync (opens a pull request) for one repository. */
export async function POST(req: Request, ctx: { params: Promise<{ id: string }> }) {
  if (!sameOrigin(req)) return NextResponse.json({ error: "Bad request." }, { status: 403 });
  const v = await viewer(req.headers);
  if (!v) return NextResponse.json({ error: "Sign in first." }, { status: 401 });
  const { id } = await ctx.params;
  if (!/^\d{1,20}$/.test(id)) return NextResponse.json({ error: "No such repository." }, { status: 404 });
  const parsed = Body.safeParse(await req.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ error: "kind must be preview or sync." }, { status: 400 });
  // A sync pushes a branch and opens a pull request as the bot; a preview changes nothing.
  if (parsed.data.kind === "sync" && v.role !== "owner") {
    return NextResponse.json({ error: "Only the account owner can start a sync." }, { status: 403 });
  }
  const r = await startJob(v.tenantId, id, parsed.data.kind, v.user.id);
  return "job" in r ? NextResponse.json(r, { status: 201 }) : NextResponse.json({ error: r.error }, { status: r.status });
}
