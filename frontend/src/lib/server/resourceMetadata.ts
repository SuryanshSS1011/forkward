import { serverEnv } from "./env";

/** Auth0's issuer when AUTH0_DOMAIN is set, in the form the MCP server verifies tokens against. */
export function auth0Issuer(): string | undefined {
  const domain = serverEnv("AUTH0_DOMAIN")?.trim().replace(/^https:\/\//, "").replace(/\/+$/, "");
  return domain ? `https://${domain}/` : undefined;
}

/** Adds Auth0 to the authorization servers in Better Auth's RFC 9728 metadata, so MCP clients see
 * both servers the MCP server accepts tokens from. Auth0 goes first, because clients sign in with the
 * first one. Anything other than a JSON 200 passes through. */
export async function withAuth0(res: Response, issuer: string | undefined): Promise<Response> {
  if (!issuer || res.status !== 200 || !res.headers.get("content-type")?.includes("application/json")) {
    return res;
  }
  const meta = (await res.json()) as { authorization_servers?: string[] };
  const servers = meta.authorization_servers ?? [];
  meta.authorization_servers = [issuer, ...servers.filter((s) => s !== issuer)];
  const headers = new Headers(res.headers);
  headers.delete("content-length");
  return Response.json(meta, { status: 200, headers });
}
