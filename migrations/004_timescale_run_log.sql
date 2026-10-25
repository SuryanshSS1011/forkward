-- On TimescaleDB (Tiger Cloud), run_log becomes a hypertable partitioned by ts, with an hourly
-- continuous aggregate of events per run and collection for the board. Plain Postgres skips all of
-- it, and so does a role that may not create the extension.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
    IF NOT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb') THEN
      RETURN;
    END IF;
    BEGIN
      CREATE EXTENSION timescaledb;
    EXCEPTION WHEN insufficient_privilege THEN
      RAISE NOTICE 'timescaledb is available but this role cannot create it; run_log stays a plain table';
      RETURN;
    END;
  END IF;

  -- A hypertable's unique indexes must include its time column.
  ALTER TABLE run_log DROP CONSTRAINT run_log_pkey;
  ALTER TABLE run_log ADD PRIMARY KEY (id, ts);
  PERFORM create_hypertable('run_log', by_range('ts', INTERVAL '7 days'), migrate_data => true);

  EXECUTE $v$
    CREATE MATERIALIZED VIEW run_log_hourly
    WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
    SELECT time_bucket(INTERVAL '1 hour', ts) AS bucket, run_id, collection, count(*) AS events
    FROM run_log
    GROUP BY bucket, run_id, collection
    WITH NO DATA
  $v$;
  PERFORM add_continuous_aggregate_policy('run_log_hourly',
    start_offset => INTERVAL '30 days', end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '5 minutes');
END
$$;
