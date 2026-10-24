import { NextResponse } from "next/server";
import { sameOrigin, viewer } from "@/lib/server/auth";
import { KeyError, createKey, listKeys } from "@/lib/server/keys";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** The signed-in user's tenant's API keys. Plaintext keys are never stored, so never listed. */
export async function GET(req: Request) {
  const v = await viewer(req.headers);
  if (!v) return NextResponse.json({ error: "Sign in first." }, { status: 401 });
  return NextResponse.json({ keys: await listKeys(v.tenantId) });
}

/** Creates a key and returns its plaintext, once. */
export async function POST(req: Request) {
  if (!sameOrigin(req)) return NextResponse.json({ error: "Bad request." }, { status: 403 });
  const v = await viewer(req.headers);
  if (!v) return NextResponse.json({ error: "Sign in first." }, { status: 401 });
  if (v.role !== "owner") return NextResponse.json({ error: "Only the account owner can create API keys." }, { status: 403 });
  const { name } = (await req.json().catch(() => ({}))) as { name?: unknown };
  try {
    const { key, row } = await createKey(v.tenantId, v.user.id, typeof name === "string" ? name : "");
    return NextResponse.json({ apiKey: key, key: row }, { status: 201 });
  } catch (e) {
    if (e instanceof KeyError) return NextResponse.json({ error: e.message }, { status: 400 });
    throw e;
  }
}
