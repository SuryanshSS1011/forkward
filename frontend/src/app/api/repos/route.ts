import { NextResponse } from "next/server";
import { viewer } from "@/lib/server/auth";
import { listRepos } from "@/lib/server/jobs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** The tenant's connected repositories, each with its most recent job. */
export async function GET(req: Request) {
  const v = await viewer(req.headers);
  if (!v) return NextResponse.json({ error: "Sign in first." }, { status: 401 });
  return NextResponse.json({ repositories: await listRepos(v.tenantId) }, { headers: { "Cache-Control": "no-store" } });
}
