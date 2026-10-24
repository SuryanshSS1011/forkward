import { oauthProviderAuthServerMetadata } from "@better-auth/oauth-provider";
import { getAuth } from "@/lib/server/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** RFC 8414 metadata at its path-inserted location, /.well-known/oauth-authorization-server/api/auth. */
export const GET = (req: Request) => oauthProviderAuthServerMetadata(getAuth())(req);
