import { createHash, randomBytes } from "node:crypto";
import { withTenant } from "./db";

/** A key in the exact format of billing.create_key in Python: "fw_" plus secrets.token_urlsafe(24)
 * (urlsafe base64 of 24 random bytes, no padding), stored as the hex SHA-256 of the whole key and
 * shown as its first 7 and last 4 characters. */
export function keyFromBytes(bytes: Uint8Array): { key: string; keyHash: string; keyId: string } {
  const key = "fw_" + Buffer.from(bytes).toString("base64url");
  return {
    key,
    keyHash: createHash("sha256").update(key).digest("hex"),
    keyId: `${key.slice(0, 7)}...${key.slice(-4)}`,
  };
}

export const newApiKey = () => keyFromBytes(randomBytes(24));

export type ApiKeyRow = {
  id: string;
  key_id: string;
  name: string;
  active: boolean;
  created_at: Date;
  revoked_at: Date | null;
  last_used_at: Date | null;
};

const MAX_ACTIVE_KEYS = 20;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function listKeys(tenantId: string): Promise<ApiKeyRow[]> {
  return withTenant(tenantId, async (db) => {
    const { rows } = await db.query<ApiKeyRow>(
      "SELECT id, key_id, name, active, created_at, revoked_at, last_used_at FROM api_keys " +
        "WHERE tenant_id = $1 ORDER BY active DESC, created_at DESC",
      [tenantId],
    );
    return rows;
  });
}

export class KeyError extends Error {}

/** Creates a key for the tenant and returns the plaintext once. Only its hash is stored. */
export async function createKey(tenantId: string, actor: string, name: string): Promise<{ key: string; row: ApiKeyRow }> {
  const label = name.trim();
  if (!label || label.length > 100) throw new KeyError("Give the key a name of 1 to 100 characters.");
  const { key, keyHash, keyId } = newApiKey();
  return withTenant(tenantId, async (db) => {
    const { rows: n } = await db.query<{ n: number }>("SELECT count(*)::int AS n FROM api_keys WHERE tenant_id = $1 AND active", [tenantId]);
    if (n[0].n >= MAX_ACTIVE_KEYS) throw new KeyError(`You can have at most ${MAX_ACTIVE_KEYS} active keys. Revoke one first.`);
    const { rows } = await db.query<ApiKeyRow>(
      "INSERT INTO api_keys (tenant_id, key_hash, key_id, name) VALUES ($1, $2, $3, $4) " +
        "RETURNING id, key_id, name, active, created_at, revoked_at, last_used_at",
      [tenantId, keyHash, keyId, label],
    );
    await db.query("INSERT INTO audit_events (tenant_id, actor, action, subject) VALUES ($1, $2, 'api_key.created', $3)", [tenantId, actor, keyId]);
    return { key, row: rows[0] };
  });
}

/** Revokes one of the tenant's own keys. False when no such active key exists for this tenant. */
export async function revokeKey(tenantId: string, actor: string, id: string): Promise<boolean> {
  if (!UUID_RE.test(id)) return false;
  return withTenant(tenantId, async (db) => {
    const { rows } = await db.query<{ key_id: string }>(
      "UPDATE api_keys SET active = false, revoked_at = now() WHERE id = $1 AND tenant_id = $2 AND active RETURNING key_id",
      [id, tenantId],
    );
    if (!rows[0]) return false;
    await db.query("INSERT INTO audit_events (tenant_id, actor, action, subject) VALUES ($1, $2, 'api_key.revoked', $3)", [tenantId, actor, rows[0].key_id]);
    return true;
  });
}
