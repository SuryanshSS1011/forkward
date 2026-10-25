import { getAuth } from "@/lib/server/auth";
import { auth0Issuer, withAuth0 } from "@/lib/server/resourceMetadata";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** RFC 9728 metadata at its path-inserted location, /.well-known/oauth-protected-resource/mcp. */
const GET = async (req: Request) => withAuth0(await getAuth().handler(req), auth0Issuer());
const HEAD = (req: Request) => getAuth().handler(req);
export { GET, HEAD };
