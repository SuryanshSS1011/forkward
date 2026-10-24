import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

/**
 * Tenant isolation on a real Postgres. Needs TEST_DATABASE_URL pointing at a throwaway database whose
 * role is not a superuser (superusers bypass row-level security). The public schema is dropped and
 * recreated, then migrations/*.sql are applied in filename order, as forkward/db.py does.
 */
const url = process.env.TEST_DATABASE_URL;
const MIGRATIONS = resolve(__dirname, "../../../../../migrations");

describe.skipIf(!url)("withTenant and withSystem", () => {
  let db: typeof import("../db");

  beforeAll(async () => {
    process.env.DATABASE_URL = url;
    db = await import("../db");
    const c = await db.pool().connect();
    try {
      await c.query("DROP SCHEMA public CASCADE");
      await c.query("CREATE SCHEMA public");
      for (const f of readdirSync(MIGRATIONS).filter((n) => /^\d{3}_.*\.sql$/.test(n)).sort()) {
        await c.query("BEGIN");
        await c.query(readFileSync(resolve(MIGRATIONS, f), "utf8"));
        await c.query("COMMIT");
      }
    } finally {
      c.release();
    }
    await db.withSystem(async (s) => {
      await s.query("INSERT INTO tenants (id, name) VALUES ('t_aaaaaaaaaaaaaaaa', 'A'), ('t_bbbbbbbbbbbbbbbb', 'B')");
      await s.query(
        "INSERT INTO api_keys (tenant_id, key_hash, key_id, name) VALUES " +
          "('t_aaaaaaaaaaaaaaaa', 'hash-a', 'fw_aaaa...aaaa', 'a'), ('t_bbbbbbbbbbbbbbbb', 'hash-b', 'fw_bbbb...bbbb', 'b')",
      );
    });
  });

  afterAll(async () => {
    await db?.closePool();
  });

  it("shows a tenant only its own rows, even without a WHERE clause", async () => {
    const seen = await db.withTenant("t_aaaaaaaaaaaaaaaa", async (c) => (await c.query("SELECT key_id FROM api_keys")).rows);
    expect(seen).toEqual([{ key_id: "fw_aaaa...aaaa" }]);
    const tenants = await db.withTenant("t_bbbbbbbbbbbbbbbb", async (c) => (await c.query("SELECT id FROM tenants")).rows);
    expect(tenants).toEqual([{ id: "t_bbbbbbbbbbbbbbbb" }]);
  });

  it("refuses to write a row for another tenant", async () => {
    await expect(
      db.withTenant("t_aaaaaaaaaaaaaaaa", (c) =>
        c.query("INSERT INTO api_keys (tenant_id, key_hash, key_id, name) VALUES ('t_bbbbbbbbbbbbbbbb', 'x', 'fw_xxxx...xxxx', 'x')"),
      ),
    ).rejects.toThrow(/row-level security/);
  });

  it("cannot change another tenant's rows", async () => {
    const n = await db.withTenant("t_aaaaaaaaaaaaaaaa", async (c) => (await c.query("UPDATE api_keys SET active = false WHERE key_id = 'fw_bbbb...bbbb'")).rowCount);
    expect(n).toBe(0);
  });

  it("does not leak the scope to the next transaction on the same connection", async () => {
    await db.withTenant("t_aaaaaaaaaaaaaaaa", async () => undefined);
    const c = await db.pool().connect();
    try {
      expect((await c.query("SELECT key_id FROM api_keys")).rows).toEqual([]);
    } finally {
      c.release();
    }
  });

  it("lets system code see every tenant", async () => {
    const n = await db.withSystem(async (c) => (await c.query("SELECT count(*)::int AS n FROM api_keys")).rows[0].n);
    expect(n).toBe(2);
  });

  it("rejects tenant ids Python would reject", async () => {
    await expect(db.withTenant("t_a'; DROP TABLE tenants; --", async () => 1)).rejects.toThrow(/invalid tenant id/);
    await expect(db.withTenant("", async () => 1)).rejects.toThrow(/invalid tenant id/);
  });

  it("creates one owned tenant per user and keys that only that tenant can revoke", async () => {
    const { ensureTenant } = await import("../tenants");
    const { createKey, listKeys, revokeKey } = await import("../keys");
    const [m1, m2] = await Promise.all([ensureTenant({ id: "user-1", name: "Ada" }), ensureTenant({ id: "user-1", name: "Ada" })]);
    expect(m1.tenantId).toBe(m2.tenantId);
    expect(m1.tenantId).toMatch(/^t_[0-9a-f]{16}$/);
    expect(m1.role).toBe("owner");

    const { key, row } = await createKey(m1.tenantId, "user-1", "laptop");
    expect(row.key_id).toBe(`${key.slice(0, 7)}...${key.slice(-4)}`);
    expect((await listKeys(m1.tenantId)).map((k) => k.key_id)).toEqual([row.key_id]);
    expect(await revokeKey("t_bbbbbbbbbbbbbbbb", "user-2", row.id)).toBe(false);
    expect(await revokeKey(m1.tenantId, "user-1", row.id)).toBe(true);
    expect((await listKeys(m1.tenantId))[0].active).toBe(false);
  });

  it("records a GitHub installation only after the user's own token shows they own or administer it", async () => {
    const { ensureTenant } = await import("../tenants");
    const m = await ensureTenant({ id: "user-gh", name: "Grace" });
    vi.doMock("../auth", () => ({
      viewer: async () => ({ user: { id: "user-gh", name: "Grace", email: "g@example.com" }, sessionId: "s", ...m }),
      githubUserToken: async () => "gho_test",
    }));
    const github: Record<string, unknown> = {
      "/user": { id: 7 },
      "/user/installations?per_page=100&page=1": {
        installations: [
          { id: 42, account: { id: 7, login: "grace", type: "User" }, repository_selection: "selected", suspended_at: null },
          { id: 43, account: { id: 8, login: "someone", type: "User" }, repository_selection: "all", suspended_at: null },
          { id: 44, account: { id: 9, login: "acme", type: "Organization" }, repository_selection: "all", suspended_at: null },
        ],
      },
      "/user/memberships/orgs/acme": { state: "active", role: "member" },
      "/user/installations/42/repositories?per_page=100&page=1": {
        repositories: [
          { id: 1001, full_name: "grace/linux", default_branch: "main", fork: true },
          { id: 1002, full_name: "grace/notes", default_branch: "main", fork: false },
        ],
      },
      "/repos/grace/linux": { parent: { full_name: "torvalds/linux", default_branch: "master" } },
    };
    const realFetch = globalThis.fetch;
    globalThis.fetch = (async (input: string | URL | Request) => {
      const path = String(input).replace("https://api.github.com", "");
      return path in github ? Response.json(github[path]) : new Response("not found", { status: 404 });
    }) as typeof fetch;
    try {
      const { GET } = await import("@/app/api/github/installed/route");
      const call = (id: string) => GET(new Request(`http://localhost/api/github/installed?installation_id=${id}&setup_action=install`));

      const foreign = await call("99");
      expect(foreign.headers.get("location")).toMatch(/github=not-yours$/);
      // Visible to the user, but neither their own account nor an org they administer.
      expect((await call("43")).headers.get("location")).toMatch(/github=not-admin$/);
      expect((await call("44")).headers.get("location")).toMatch(/github=not-admin$/);
      const ok = await call("42");
      expect(ok.headers.get("location")).toMatch(/github=installed$/);
    } finally {
      globalThis.fetch = realFetch;
      vi.doUnmock("../auth");
    }

    const rows = await db.withTenant(m.tenantId, async (c) => ({
      installations: (await c.query("SELECT id::int, account_login FROM installations")).rows,
      repos: (await c.query("SELECT full_name, upstream FROM repositories ORDER BY id")).rows,
      audit: (await c.query("SELECT action, subject FROM audit_events WHERE action LIKE 'github.%'")).rows,
    }));
    expect(rows.installations).toEqual([{ id: 42, account_login: "grace" }]);
    expect(rows.repos).toEqual([
      { full_name: "grace/linux", upstream: "torvalds/linux@master" },
      { full_name: "grace/notes", upstream: null },
    ]);
    expect(rows.audit).toEqual([{ action: "github.installation.installed", subject: "42" }]);
    const others = await db.withTenant("t_aaaaaaaaaaaaaaaa", async (c) => (await c.query("SELECT count(*)::int AS n FROM repositories")).rows[0].n);
    expect(others).toBe(0);
  });
});

describe("checkRole", () => {
  it("refuses a role that bypasses row-level security", async () => {
    const { checkRole } = await import("../db");
    const as = (bypass: boolean) => ({ query: async () => ({ rows: [{ bypass }] }) }) as never;
    await expect(checkRole(as(false))).resolves.toBeUndefined();
    await expect(checkRole(as(true))).rejects.toThrow(/bypasses row-level security/);
  });
});

describe.skipIf(!url)("Stripe subscriptions", () => {
  const TENANT = "t_stripe0000000000";
  type Sub = { id: string; status: string; created: number; customer: string; items: { data: { price: { id: string } }[] }; metadata: Record<string, string> };
  const subs: Record<string, Sub> = {};
  const cancelled: string[] = [];
  const sub = (n: number): Sub => ({
    id: `sub_${n}`,
    status: "active",
    created: n,
    customer: "cus_1",
    items: { data: [{ price: { id: "price_team" } }] },
    metadata: { tenant_id: TENANT },
  });
  const checkout = (s: Sub) =>
    ({
      id: `evt_co_${s.id}`,
      type: "checkout.session.completed",
      data: { object: { mode: "subscription", client_reference_id: TENANT, metadata: { plan: "team" }, customer: "cus_1", subscription: s.id } },
    }) as never;
  const created = (s: Sub) => ({ id: `evt_cr_${s.id}`, type: "customer.subscription.created", data: { object: s } }) as never;
  let stripeLib: typeof import("../stripe");
  let db: typeof import("../db");

  beforeAll(async () => {
    process.env.DATABASE_URL = url;
    Object.assign(process.env, { STRIPE_SECRET_KEY: "sk_test", STRIPE_PRICE_TEAM_BASE: "price_team", STRIPE_PRICE_TEAM_METERED: "price_team_m" });
    vi.resetModules();
    vi.doMock("stripe", () => ({
      default: class {
        subscriptions = {
          retrieve: async (id: string) => subs[id],
          cancel: async (id: string) => {
            cancelled.push(id);
            subs[id] = { ...subs[id], status: "canceled" };
            return subs[id];
          },
        };
      },
    }));
    db = await import("../db");
    stripeLib = await import("../stripe");
    await db.withSystem((s) => s.query("INSERT INTO tenants (id, name) VALUES ($1, 'S') ON CONFLICT DO NOTHING", [TENANT]));
  });

  afterAll(async () => {
    vi.doUnmock("stripe");
    for (const k of ["STRIPE_SECRET_KEY", "STRIPE_PRICE_TEAM_BASE", "STRIPE_PRICE_TEAM_METERED"]) delete process.env[k];
    await db?.closePool();
  });

  it("keeps one live subscription when two checkouts complete", async () => {
    const current = async () =>
      (await db.withSystem((s) => s.query("SELECT plan, stripe_subscription_id AS sub FROM tenants WHERE id = $1", [TENANT]))).rows[0];
    for (const s of [sub(100), sub(200), sub(300)]) subs[s.id] = s;

    await stripeLib.applyStripeEvent(checkout(subs.sub_100));
    expect(await current()).toEqual({ plan: "team", sub: "sub_100" });

    // A second checkout completes: the newer subscription is cancelled, the tenant keeps the first.
    expect(await stripeLib.applyStripeEvent(checkout(subs.sub_200))).toMatch(/cancelled duplicate sub_200/);
    // A third one whose subscription event arrives before its checkout event.
    expect(await stripeLib.applyStripeEvent(created(subs.sub_300))).toMatch(/cancelled duplicate sub_300/);
    expect(await stripeLib.applyStripeEvent(checkout(subs.sub_300))).toBe("ignored ended subscription");

    expect(cancelled).toEqual(["sub_200", "sub_300"]);
    expect(await current()).toEqual({ plan: "team", sub: "sub_100" });
  });
});

describe("owner-only writes", () => {
  it("refuses members every action that pushes or changes account settings", async () => {
    vi.resetModules();
    vi.doMock("../auth", async (orig) => ({
      ...(await orig<typeof import("../auth")>()),
      viewer: async () => ({ user: { id: "user-m", name: "M", email: "m@example.com" }, sessionId: "s", tenantId: "t_member000000000", role: "member", plan: "team" }),
    }));
    const req = (method: string, body?: unknown) =>
      new Request("http://localhost/api/x", {
        method,
        headers: { origin: "http://localhost", host: "localhost", "content-type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    const params = <T>(p: T) => ({ params: Promise.resolve(p) });
    try {
      const job = "00000000-0000-4000-8000-000000000000";
      const responses = [
        await (await import("@/app/api/jobs/[id]/apply/route")).POST(req("POST"), params({ id: job })),
        await (await import("@/app/api/repos/[id]/route")).PATCH(req("PATCH", { enabled: false }), params({ id: "1" })),
        await (await import("@/app/api/repos/[id]/jobs/route")).POST(req("POST", { kind: "sync" }), params({ id: "1" })),
        await (await import("@/app/api/keys/route")).POST(req("POST", { name: "ci" })),
        await (await import("@/app/api/keys/[id]/route")).DELETE(req("DELETE"), params({ id: "1" })),
      ];
      expect(responses.map((r) => r.status)).toEqual([403, 403, 403, 403, 403]);
      const install = await (await import("@/app/api/github/installed/route")).GET(
        new Request("http://localhost/api/github/installed?installation_id=42&setup_action=install"),
      );
      expect(install.headers.get("location")).toMatch(/github=not-owner$/);
    } finally {
      vi.doUnmock("../auth");
    }
  });
});
