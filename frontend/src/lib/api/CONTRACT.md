# Frontend contract

What the frontend reads from the Next.js server (`src/app/api`). Every customer-facing view reads
Postgres, which the Python worker writes as it runs (migrations 001 and 003). The web app never
talks to the research board (`dashboard/server.py`); that stays an operator tool on localhost.

## Repositories and jobs

Every route needs a session and answers 401 without one. POST and PATCH also need a same-origin
`Origin` header. Rows are scoped to the caller's tenant by row-level security.

| Method and path | Shape | Adapter |
|---|---|---|
| `GET /api/repos` | `{ repositories: RepoRow[] }`, each with its most recent job as `latest` | `api/jobs.ts` |
| `PATCH /api/repos/:id` | body `{ upstream: "owner/name@ref" \| null, schedule: "hourly" \| "daily" \| "weekly" \| null }` | `api/jobs.ts` |
| `POST /api/repos/:id/jobs` | body `{ kind: "preview" \| "sync" }`, returns `{ job }` (201). 409 when the repository has no upstream or a job is already running | `api/jobs.ts` |
| `GET /api/jobs` | `{ jobs: JobSummary[] }`, newest first | `api/jobs.ts` |
| `GET /api/jobs/:id?after=<event id>` | `JobDetail`: the job, every hunk with its detail, and the events after `after` | `useJob.ts` |
| `POST /api/jobs/:id/hunks/:hunk` | body `{ decision: "accepted" \| "rejected" \| null, resolution? }`. 409 for a hunk that was applied automatically or is already committed | `api/jobs.ts` |
| `POST /api/jobs/:id/apply` | Commits every accepted hunk to the sync branch through the worker (202). 409 for a preview or a closed pull request | `api/jobs.ts` |

Shapes are in `src/lib/jobs.ts`. A job's `stage` is one of fetch, merge, resolve, open_pr, ci,
review and done, and `progress` is `{ done, total }` while hunks are being resolved. The web app
starts work only by writing rows: a new `jobs` row with no workflow, or `review_requested` raised
past `review_applied`. The worker claims both within about two seconds.

`useJob` polls every 1.5 s while a job runs and every 10 s while it waits on CI or a reviewer, and
stops once it has finished. Events are fetched incrementally and merged by id.

## Account endpoints (Next.js)

Sign-in is Better Auth with GitHub only, at `/api/auth/*` (use `src/lib/auth-client.ts`, not raw
fetch). Every route below needs a session and answers 401 without one. POST and DELETE also need a
same-origin `Origin` header.

| Method and path | Shape | Adapter |
|---|---|---|
| `GET /api/me` | `{ user: { id, name, email, image, tenantId, role, plan } }`, or `{ user: null }` (200) when signed out | `components/useMe.ts` |
| `GET /api/keys` | `{ keys: ApiKey[] }`, newest active first | `api/keys.ts` |
| `POST /api/keys` | body `{ name }`, returns `{ apiKey, key }`. `apiKey` is the plaintext, shown once | `api/keys.ts` |
| `DELETE /api/keys/:id` | `{ ok: true }`, or 404 when the key is not this tenant's or already revoked | `api/keys.ts` |
| `GET /api/github/repositories` | `{ repositories: [{ full_name, upstream, account_login }] }` | `app/connect/ConnectClient.tsx` |
| `GET /api/github/installed?installation_id=&setup_action=` | The GitHub App's Setup URL. Redirects to `/repos?github=<status>` | none |
| `GET /api/billing/usage` | `{ plan, role, hasBilling, periodStart, unitsUsed, unitsReserved, unitsIncluded, unitsLimit, unitsLeft, amountDueUsd, byTool }` | `api/billing.ts` |
| `POST /api/billing/checkout` | body `{ plan: "team"\|"enterprise" }`, returns `{ url }` (Stripe Checkout, or the portal when already subscribed). Owners only | `api/billing.ts` |
| `POST /api/billing/portal` | returns `{ url }`. Owners only | `api/billing.ts` |
| `POST /api/stripe/webhook` | Stripe only. Verified with `STRIPE_WEBHOOK_SECRET` | none |

`ApiKey` is `{ id, key_id, name, active, created_at, revoked_at, last_used_at }`. Plans and their
numbers come from `src/lib/plans.ts`, which a test keeps equal to `PLANS` in `forkward/billing.py`.

## OAuth for the hosted MCP server

Better Auth is the OAuth 2.1 authorization server. Its issuer is `<BETTER_AUTH_URL>/api/auth`.

| Path | What |
|---|---|
| `/.well-known/oauth-authorization-server` and `/.well-known/oauth-authorization-server/api/auth` | RFC 8414 metadata |
| `/api/auth/.well-known/openid-configuration` | OpenID Connect discovery |
| `/.well-known/oauth-protected-resource` and `/.well-known/oauth-protected-resource/mcp` | RFC 9728 metadata for the resource `<BETTER_AUTH_URL>/mcp` |
| `/api/auth/jwks` | Keys that sign access tokens (EdDSA by default) |
| `/api/auth/oauth2/authorize`, `/token`, `/revoke`, `/introspect`, `/userinfo` | Provider endpoints |
| `/signin`, `/consent` | The login and consent pages the provider redirects to |
