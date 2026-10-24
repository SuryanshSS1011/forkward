import { oauthProviderAuthServerMetadata } from "@better-auth/oauth-provider";
import { getAuth } from "@/lib/server/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** RFC 8414 authorization server metadata. The issuer is <origin>/api/auth, so the RFC location is
 * /.well-known/oauth-authorization-server/api/auth (the [...issuer] route); this bare path serves
 * the same document for MCP clients that look at the root. */
export const GET = (req: Request) => oauthProviderAuthServerMetadata(getAuth())(req);
