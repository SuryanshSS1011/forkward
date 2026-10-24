#!/usr/bin/env node
// Development and end-to-end tests only: create (or reuse) a user with a tenant and an installed
// demo repository, open a Better Auth session for them, and print the signed session cookie.
// GitHub sign-in cannot run without a real GitHub App, so this stands in for it locally.
//
//   node scripts/dev-session.mjs            -> prints better-auth.session_token=<signed value>
//
// Refuses to run when NODE_ENV is production. Reads DATABASE_URL and BETTER_AUTH_SECRET from the
// environment or the repository's root .env.
import { createHmac, randomBytes } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import pg from "pg";

if (process.env.NODE_ENV === "production") {
  console.error("dev-session refuses to run in production");
  process.exit(1);
}
const rootEnv = resolve(import.meta.dirname, "../../.env");
if (existsSync(rootEnv)) {
  for (const line of readFileSync(rootEnv, "utf8").split("\n")) {
    const m = line.match(/^([A-Z_][A-Z0-9_]*)=(.*)$/);
    if (m && process.env[m[1]] === undefined) process.env[m[1]] = m[2];
  }
}
const { DATABASE_URL, BETTER_AUTH_SECRET } = process.env;
if (!DATABASE_URL || !BETTER_AUTH_SECRET) {
  console.error("DATABASE_URL and BETTER_AUTH_SECRET are required");
  process.exit(1);
}

const USER = { id: "dev-user", name: "Dev User", email: "dev@example.com" };
const TENANT = "t_00000000000000de";
const REPO = { id: 900001, installation: 900001, full_name: "dev-org/linux-fork", upstream: "gregkh/linux@linux-6.6.y" };

const db = new pg.Client({ connectionString: DATABASE_URL });
await db.connect();
try {
  await db.query("BEGIN");
  await db.query("SELECT set_config('app.role', 'system', true)");
  await db.query(
    `INSERT INTO "user" (id, name, email, "emailVerified", "createdAt", "updatedAt")
     VALUES ($1, $2, $3, true, now(), now()) ON CONFLICT (id) DO NOTHING`,
    [USER.id, USER.name, USER.email],
  );
  await db.query(`INSERT INTO tenants (id, name, plan) VALUES ($1, $2, 'team') ON CONFLICT (id) DO NOTHING`, [TENANT, "Dev Org"]);
  await db.query(
    `INSERT INTO tenant_members (tenant_id, user_id, role) VALUES ($1, $2, 'owner') ON CONFLICT DO NOTHING`,
    [TENANT, USER.id],
  );
  await db.query(
    `INSERT INTO installations (id, tenant_id, account_login, account_type) VALUES ($1, $2, 'dev-org', 'Organization')
     ON CONFLICT (id) DO NOTHING`,
    [REPO.installation, TENANT],
  );
  await db.query(
    `INSERT INTO repositories (id, installation_id, tenant_id, full_name, default_branch, upstream, schedule)
     VALUES ($1, $2, $3, $4, 'main', $5, 'weekly') ON CONFLICT (id) DO NOTHING`,
    [REPO.id, REPO.installation, TENANT, REPO.full_name, REPO.upstream],
  );
  const token = randomBytes(24).toString("base64url");
  await db.query(
    `INSERT INTO session (id, "expiresAt", token, "createdAt", "updatedAt", "userId")
     VALUES ($1, now() + interval '7 days', $2, now(), now(), $3)`,
    [randomBytes(12).toString("hex"), token, USER.id],
  );
  await db.query("COMMIT");
  // Better Auth signs the cookie as better-call does: value + "." + base64(HMAC-SHA256), URI-encoded.
  const sig = createHmac("sha256", BETTER_AUTH_SECRET).update(token).digest("base64");
  console.log(`better-auth.session_token=${encodeURIComponent(`${token}.${sig}`)}`);
} catch (e) {
  await db.query("ROLLBACK");
  throw e;
} finally {
  await db.end();
}
