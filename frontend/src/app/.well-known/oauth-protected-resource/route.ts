import { getAuth } from "@/lib/server/auth";
import { auth0Issuer, withAuth0 } from "@/lib/server/resourceMetadata";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** RFC 9728 protected resource metadata for the hosted MCP server at <origin>/mcp. The mcp() plugin
 * answers both /.well-known/oauth-protected-resource and /.well-known/oauth-protected-resource/mcp. */
const GET = async (req: Request) => withAuth0(await getAuth().handler(req), auth0Issuer());
const HEAD = (req: Request) => getAuth().handler(req);
export { GET, HEAD };
