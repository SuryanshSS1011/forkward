import { Pool, type PoolClient } from "pg";
import { serverEnv } from "./env";

/**
 * Postgres access for the web app, mirroring forkward/db.py.
 *
 * Every customer table has forced row-level security (migrations/001_core.sql), so each
 * transaction says whose rows it may touch. `withTenant` sees one tenant's rows and `withSystem`
 * sees every tenant's rows, for trusted server code such as the Stripe webhook. Both run one
 * transaction with the same set_config calls as Python, which last until that transaction ends.
 */

/** The same pattern as `_TENANT` in forkward/db.py. */
export const TENANT_RE = /^[A-Za-z0-9_:.-]{1,128}$/;

export function validTenantId(id: unknown): id is string {
  return typeof id === "string" && TENANT_RE.test(id);
}

const globalForPg = globalThis as unknown as { _forkwardPg?: Pool; _forkwardRoleOk?: boolean };

/** One pool per server process, reused across requests and hot reloads. Opened on first use, so a
 * build without DATABASE_URL never touches the network. */
export function pool(): Pool {
  if (!globalForPg._forkwardPg) {
    const connectionString = serverEnv("DATABASE_URL");
    if (!connectionString) throw new Error("DATABASE_URL is not set");
    globalForPg._forkwardPg = new Pool({
      connectionString,
      max: Number(serverEnv("DB_POOL_MAX") ?? 8),
      application_name: "forkward-web",
      connectionTimeoutMillis: 10_000,
    });
  }
  return globalForPg._forkwardPg;
}

/** Closes the pool. Tests call it so the process can exit. */
export async function closePool(): Promise<void> {
  const p = globalForPg._forkwardPg;
  globalForPg._forkwardPg = undefined;
  globalForPg._forkwardRoleOk = undefined;
  await p?.end();
}

/** Refuses a role that skips row-level security (a superuser, or BYPASSRLS as some hosted Postgres
 * owner roles have), since every tenant would then see every other tenant's rows. */
export async function checkRole(client: Pick<PoolClient, "query">): Promise<void> {
  const r = await client.query("SELECT rolsuper OR rolbypassrls AS bypass FROM pg_roles WHERE rolname = current_user");
  if (r.rows[0]?.bypass) {
    throw new Error("the DATABASE_URL role bypasses row-level security (superuser or BYPASSRLS); connect as a role with neither");
  }
}

async function scoped<T>(settings: [string, string][], fn: (client: PoolClient) => Promise<T>): Promise<T> {
  const client = await pool().connect();
  try {
    if (!globalForPg._forkwardRoleOk) {
      await checkRole(client);
      globalForPg._forkwardRoleOk = true;
    }
    await client.query("BEGIN");
    for (const [k, v] of settings) await client.query("SELECT set_config($1, $2, true)", [k, v]);
    const out = await fn(client);
    await client.query("COMMIT");
    return out;
  } catch (e) {
    await client.query("ROLLBACK").catch(() => undefined);
    throw e;
  } finally {
    client.release();
  }
}

/** A transaction that can read and write only `tenantId`'s rows. */
export function withTenant<T>(tenantId: string, fn: (client: PoolClient) => Promise<T>): Promise<T> {
  if (!validTenantId(tenantId)) return Promise.reject(new Error("invalid tenant id"));
  return scoped(
    [
      ["app.tenant_id", tenantId],
      ["app.role", "tenant"],
    ],
    fn,
  );
}

/** A transaction that may touch every tenant. Only for trusted server code. */
export function withSystem<T>(fn: (client: PoolClient) => Promise<T>): Promise<T> {
  return scoped(
    [
      ["app.role", "system"],
      ["app.tenant_id", ""],
    ],
    fn,
  );
}
