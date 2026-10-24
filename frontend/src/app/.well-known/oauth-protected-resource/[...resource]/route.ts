import { getAuth } from "@/lib/server/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** RFC 9728 metadata at its path-inserted location, /.well-known/oauth-protected-resource/mcp. */
const handle = (req: Request) => getAuth().handler(req);
export { handle as GET, handle as HEAD };
