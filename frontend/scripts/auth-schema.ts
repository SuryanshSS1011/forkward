// Loaded only by the Better Auth CLI to print the schema SQL for migrations/002_better_auth.sql:
//   DATABASE_URL=... BETTER_AUTH_SECRET=... npx auth generate --config scripts/auth-schema.ts
// The running app never migrates its own schema.
import { getAuth } from "../src/lib/server/auth";

export const auth = getAuth();
