import { afterEach, describe, expect, it, vi } from "vitest";
import { auth0Issuer, withAuth0 } from "../resourceMetadata";

const meta = (servers: string[]) =>
  Response.json({ resource: "https://mergesthat.work/mcp", authorization_servers: servers });

describe("protected resource metadata", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("normalizes AUTH0_DOMAIN to the issuer the MCP server verifies", () => {
    vi.stubEnv("AUTH0_DOMAIN", "https://tenant.us.auth0.com/");
    expect(auth0Issuer()).toBe("https://tenant.us.auth0.com/");
    vi.stubEnv("AUTH0_DOMAIN", "tenant.us.auth0.com");
    expect(auth0Issuer()).toBe("https://tenant.us.auth0.com/");
  });

  it("lists Auth0 first and once", async () => {
    const issuer = "https://tenant.us.auth0.com/";
    const res = await withAuth0(meta(["https://mergesthat.work/api/auth"]), issuer);
    const body = await res.json();
    expect(body.authorization_servers).toEqual([issuer, "https://mergesthat.work/api/auth"]);
    expect(body.resource).toBe("https://mergesthat.work/mcp");
    const again = await withAuth0(meta(["https://mergesthat.work/api/auth", issuer]), issuer);
    expect((await again.json()).authorization_servers).toEqual([issuer, "https://mergesthat.work/api/auth"]);
  });

  it("passes the response through without Auth0 or on errors", async () => {
    const plain = meta(["https://mergesthat.work/api/auth"]);
    expect(await withAuth0(plain, undefined)).toBe(plain);
    const missing = new Response("not found", { status: 404 });
    expect(await withAuth0(missing, "https://tenant.us.auth0.com/")).toBe(missing);
  });
});
