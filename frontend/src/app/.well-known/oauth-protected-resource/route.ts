import { getAuth } from "@/lib/server/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** RFC 9728 protected resource metadata for the hosted MCP server at <origin>/mcp. The mcp() plugin
 * answers both /.well-known/oauth-protected-resource and /.well-known/oauth-protected-resource/mcp. */
const handle = (req: Request) => getAuth().handler(req);
export { handle as GET, handle as HEAD };
