import { getAuth } from "@/lib/server/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Every Better Auth endpoint: GitHub sign-in, sessions, the OAuth 2.1 provider and the JWKS. */
const handle = (req: Request) => getAuth().handler(req);
export { handle as GET, handle as POST, handle as PATCH, handle as PUT, handle as DELETE };
