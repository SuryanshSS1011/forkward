-- Jobs as the web app sees them: live stage and progress, a compact event log per job, per-hunk
-- detail for the review screen, and the reviewer's decision on each hunk.

-- A preview resolves conflicts without pushing anything.
ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check
  CHECK (kind IN ('sync', 'resolve', 'repair', 'preview'));

ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_status_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_status_check
  CHECK (status IN ('queued', 'running', 'waiting_ci', 'waiting_approval', 'applying',
                    'succeeded', 'failed', 'cancelled'));

-- stage: fetch, merge, resolve, open_pr, ci, review, done. progress: {"done": n, "total": m}.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS stage text;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS progress jsonb NOT NULL DEFAULT '{}';
-- The web app asks the worker to commit reviewed hunks by bumping review_requested; the worker
-- records review_applied when the commit is pushed.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS review_requested integer NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS review_applied integer NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS job_events (
  id        bigserial PRIMARY KEY,
  job_id    uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  ts        timestamptz NOT NULL DEFAULT now(),
  stage     text,
  kind      text NOT NULL,  -- stage, progress, info, warning, error, done
  message   text NOT NULL,
  data      jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS job_events_job ON job_events (job_id, id);

ALTER TABLE job_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON job_events
  USING (forkward_scope_ok(tenant_id)) WITH CHECK (forkward_scope_ok(tenant_id));

-- detail: {"vendor": [...], "upstream": [...], "candidates": [{"lines": [...], "sources": [...],
-- "votes": n, "label": "...", "chosen": bool}], "classifier": "...", "margin": n}
ALTER TABLE hunks ADD COLUMN IF NOT EXISTS hunk_ref text;  -- file#n as git numbered the block
ALTER TABLE hunks ADD COLUMN IF NOT EXISTS reason text;
ALTER TABLE hunks ADD COLUMN IF NOT EXISTS detail jsonb NOT NULL DEFAULT '{}';
ALTER TABLE hunks ADD COLUMN IF NOT EXISTS review text CHECK (review IN ('accepted', 'rejected'));
ALTER TABLE hunks ADD COLUMN IF NOT EXISTS final_resolution text;
ALTER TABLE hunks ADD COLUMN IF NOT EXISTS reviewed_at timestamptz;
ALTER TABLE hunks ADD COLUMN IF NOT EXISTS review_commit text;
