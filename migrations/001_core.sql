-- Forkward core schema. Applied in order by `python -m forkward db migrate`.
--
-- Tenancy. Every customer-owned row carries tenant_id, and row-level security is forced on those
-- tables, so a query sees rows only after the connection sets app.tenant_id (or app.role to
-- 'system' for workers and the CLI) inside its transaction. A query that forgets to scope itself
-- sees nothing, which is the second barrier behind the application's own checks.
--
-- Identity tables (user, session, account, verification and the OAuth tables) belong to Better Auth
-- and are created by its own migration; tenant_members.user_id refers to them by id.

CREATE OR REPLACE FUNCTION forkward_scope_ok(row_tenant text) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT coalesce(current_setting('app.role', true), '') = 'system'
      OR row_tenant = nullif(current_setting('app.tenant_id', true), '')
$$;

CREATE TABLE tenants (
  id                     text PRIMARY KEY,
  name                   text NOT NULL,
  plan                   text NOT NULL DEFAULT 'trial',
  stripe_customer_id     text UNIQUE,
  stripe_subscription_id text,
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tenant_members (
  tenant_id  text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  user_id    text NOT NULL,
  role       text NOT NULL DEFAULT 'owner' CHECK (role IN ('owner', 'member')),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, user_id)
);

CREATE TABLE api_keys (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  key_hash     text NOT NULL UNIQUE,
  key_id       text NOT NULL UNIQUE,  -- display form, e.g. fw_abcd...wxyz
  name         text NOT NULL,
  active       boolean NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now(),
  revoked_at   timestamptz,
  last_used_at timestamptz
);

-- GitHub App installations and the repositories each one grants.
CREATE TABLE installations (
  id                   bigint PRIMARY KEY,  -- GitHub installation id
  tenant_id            text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  account_login        text NOT NULL,
  account_type         text NOT NULL,
  repository_selection text NOT NULL DEFAULT 'selected',
  suspended_at         timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE repositories (
  id              bigint PRIMARY KEY,  -- GitHub repository id
  installation_id bigint NOT NULL REFERENCES installations(id) ON DELETE CASCADE,
  tenant_id       text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  full_name       text NOT NULL,
  default_branch  text,
  upstream        text,  -- owner/name@ref the fork follows
  schedule        text,  -- cron expression, null when only webhooks or manual runs sync it
  enabled         boolean NOT NULL DEFAULT true,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX repositories_tenant ON repositories (tenant_id);
CREATE UNIQUE INDEX repositories_full_name ON repositories (lower(full_name));

-- One sync or resolve request. workflow_id links to the durable workflow that runs it.
CREATE TABLE jobs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  repository_id bigint REFERENCES repositories(id) ON DELETE SET NULL,
  repo          text NOT NULL,
  kind          text NOT NULL CHECK (kind IN ('sync', 'resolve', 'repair')),
  status        text NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'running', 'waiting_ci', 'waiting_approval',
                                  'succeeded', 'failed', 'cancelled')),
  upstream_ref  text,
  base          text,
  branch        text,
  pr_url        text,
  model         text,
  workflow_id   text UNIQUE,
  summary       jsonb NOT NULL DEFAULT '{}',
  error         text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX jobs_tenant_created ON jobs (tenant_id, created_at DESC);

-- One conflict hunk in a job, with the gate's decision.
CREATE TABLE hunks (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id          uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  tenant_id       text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  file            text NOT NULL,
  idx             integer NOT NULL,
  conflict_sha256 text NOT NULL,
  patch_id        text,
  decision        text CHECK (decision IN ('auto', 'review', 'error')),
  confidence      double precision,
  threshold       double precision,
  source          text,
  resolution      text,
  applied         boolean NOT NULL DEFAULT false,
  approved_by     text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (job_id, file, idx)
);
CREATE INDEX hunks_tenant ON hunks (tenant_id);

CREATE TABLE candidates (
  id        bigserial PRIMARY KEY,
  hunk_id   uuid NOT NULL REFERENCES hunks(id) ON DELETE CASCADE,
  tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  sources   text[] NOT NULL,
  label     text,
  lines     text NOT NULL,
  votes     integer NOT NULL DEFAULT 0,
  evidence  jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX candidates_hunk ON candidates (hunk_id);

-- Resolutions a tenant has verified, reused when the same upstream change conflicts again.
CREATE TABLE resolution_cache (
  tenant_id     text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  conflict_key  text NOT NULL,  -- SHA-256 of the normalized conflict
  patch_id      text,
  resolution    text NOT NULL,
  verified_by   text NOT NULL,  -- 'ci', 'human' or 'merged'
  hits          integer NOT NULL DEFAULT 0,
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_hit_at   timestamptz,
  PRIMARY KEY (tenant_id, conflict_key)
);
CREATE INDEX resolution_cache_patch ON resolution_cache (tenant_id, patch_id);

-- Billing. Reservations move usage_quota atomically before any model call; usage_events is the
-- ledger and the outbox the Stripe meter reporter drains (stripe_reported_at marks sent rows).
CREATE TABLE usage_quota (
  tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  month     date NOT NULL,
  used      integer NOT NULL DEFAULT 0 CHECK (used >= 0),
  PRIMARY KEY (tenant_id, month)
);

CREATE TABLE usage_events (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  key_id             text,
  tool               text NOT NULL,
  units              integer NOT NULL CHECK (units >= 0),
  reserved           integer NOT NULL DEFAULT 0,
  repo               text,
  job_id             uuid,
  detail             jsonb NOT NULL DEFAULT '{}',
  ts                 timestamptz NOT NULL DEFAULT now(),
  stripe_reported_at timestamptz
);
CREATE INDEX usage_events_tenant_ts ON usage_events (tenant_id, ts DESC);
CREATE INDEX usage_events_unreported ON usage_events (ts) WHERE stripe_reported_at IS NULL;

-- Append-only audit trail of every decision and action taken on a customer's behalf.
CREATE TABLE audit_events (
  id        bigserial PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  actor     text NOT NULL,  -- user id, 'forkward-bot' or 'system'
  action    text NOT NULL,
  subject   text,
  detail    jsonb NOT NULL DEFAULT '{}',
  ts        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_events_tenant_ts ON audit_events (tenant_id, ts DESC);

CREATE OR REPLACE FUNCTION forkward_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_events is append-only';
END
$$;
CREATE TRIGGER audit_events_append_only BEFORE UPDATE OR DELETE ON audit_events
  FOR EACH ROW EXECUTE FUNCTION forkward_append_only();

-- Research run log. The JSONL files under runs/ stay the source of truth; this mirrors them
-- without full prompts and responses. Not tenant data, so no row-level security.
CREATE TABLE run_log (
  id         bigserial PRIMARY KEY,
  run_id     text NOT NULL,
  collection text NOT NULL
             CHECK (collection IN ('events', 'attempts', 'strategies', 'playbook_versions',
                                   'rounds', 'evals')),
  ts         timestamptz NOT NULL,
  doc        jsonb NOT NULL
);
CREATE INDEX run_log_run ON run_log (run_id, collection, id);

-- Row-level security on every tenant table.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['tenants', 'tenant_members', 'api_keys', 'installations',
                           'repositories', 'jobs', 'hunks', 'candidates', 'resolution_cache',
                           'usage_quota', 'usage_events', 'audit_events']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_scope ON %I USING (forkward_scope_ok(%s)) WITH CHECK (forkward_scope_ok(%s))',
      t, CASE WHEN t = 'tenants' THEN 'id' ELSE 'tenant_id' END,
         CASE WHEN t = 'tenants' THEN 'id' ELSE 'tenant_id' END);
  END LOOP;
END
$$;
