import { NextResponse } from "next/server";
import { viewer } from "@/lib/server/auth";
import { listJobs } from "@/lib/server/jobs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const v = await viewer(req.headers);
  if (!v) return NextResponse.json({ error: "Sign in first." }, { status: 401 });
  return NextResponse.json({ jobs: await listJobs(v.tenantId) }, { headers: { "Cache-Control": "no-store" } });
}
