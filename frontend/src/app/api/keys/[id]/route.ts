import { NextResponse } from "next/server";
import { sameOrigin, viewer } from "@/lib/server/auth";
import { revokeKey } from "@/lib/server/keys";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Revokes one of the tenant's own keys. The hosted MCP server rejects it from the next call. */
export async function DELETE(req: Request, { params }: { params: Promise<{ id: string }> }) {
  if (!sameOrigin(req)) return NextResponse.json({ error: "Bad request." }, { status: 403 });
  const v = await viewer(req.headers);
  if (!v) return NextResponse.json({ error: "Sign in first." }, { status: 401 });
  if (v.role !== "owner") return NextResponse.json({ error: "Only the account owner can revoke API keys." }, { status: 403 });
  const { id } = await params;
  if (!(await revokeKey(v.tenantId, v.user.id, id))) return NextResponse.json({ error: "No such active key." }, { status: 404 });
  return NextResponse.json({ ok: true });
}
