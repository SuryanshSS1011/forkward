import { randomBytes } from "node:crypto";
import { withSystem } from "./db";

export type Membership = { tenantId: string; role: "owner" | "member"; plan: string };

/** The same shape as billing.new_tenant_id() in Python: "t_" and 16 hex characters. */
export function newTenantId(): string {
  return "t_" + randomBytes(8).toString("hex");
}

/** The tenant a user belongs to, or null. Membership lookup runs before any tenant is known, so it
 * reads tenant_members with the system role. */
export async function membership(userId: string): Promise<Membership | null> {
  return withSystem(async (db) => {
    const { rows } = await db.query<{ tenant_id: string; role: "owner" | "member"; plan: string }>(
      "SELECT m.tenant_id, m.role, t.plan FROM tenant_members m JOIN tenants t ON t.id = m.tenant_id " +
        "WHERE m.user_id = $1 ORDER BY m.created_at LIMIT 1",
      [userId],
    );
    return rows[0] ? { tenantId: rows[0].tenant_id, role: rows[0].role, plan: rows[0].plan } : null;
  });
}

/** Gives a user their own tenant, with them as its owner, unless they already belong to one.
 * Runs from the user-create database hook on first sign-in, and again on any later request that
 * finds no membership, so a failed hook never leaves a user without a tenant. A per-user advisory
 * lock keeps two concurrent first requests from creating two tenants. */
export async function ensureTenant(user: { id: string; name?: string | null; email?: string | null }): Promise<Membership> {
  return withSystem(async (db) => {
    await db.query("SELECT pg_advisory_xact_lock(hashtext('forkward-tenant:' || $1))", [user.id]);
    const existing = await db.query<{ tenant_id: string; role: "owner" | "member"; plan: string }>(
      "SELECT m.tenant_id, m.role, t.plan FROM tenant_members m JOIN tenants t ON t.id = m.tenant_id " +
        "WHERE m.user_id = $1 ORDER BY m.created_at LIMIT 1",
      [user.id],
    );
    if (existing.rows[0]) {
      const r = existing.rows[0];
      return { tenantId: r.tenant_id, role: r.role, plan: r.plan };
    }
    const tenantId = newTenantId();
    const name = (user.name || user.email || user.id).slice(0, 200);
    await db.query("INSERT INTO tenants (id, name) VALUES ($1, $2)", [tenantId, name]);
    await db.query("INSERT INTO tenant_members (tenant_id, user_id, role) VALUES ($1, $2, 'owner')", [tenantId, user.id]);
    await db.query("INSERT INTO audit_events (tenant_id, actor, action, subject) VALUES ($1, $2, 'tenant.created', $2)", [
      tenantId,
      user.id,
    ]);
    return { tenantId, role: "owner", plan: "trial" };
  });
}
