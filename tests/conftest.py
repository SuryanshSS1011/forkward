"""Shared fixtures. `pg` gives a freshly migrated Postgres database.

Database tests need TEST_DATABASE_URL pointing at a throwaway database whose role is not a
superuser (superusers bypass row-level security). They skip when it is unset. CI runs a Postgres
service container for them.
"""

import os

import psycopg
import pytest

from forkward import db

# A developer's .env may hold the production DATABASE_URL, and llm.load_env() fills in any variable
# that is unset. An empty value keeps every test off it; only the pg fixture sets a database.
os.environ["DATABASE_URL"] = ""


@pytest.fixture
def pg(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    db.close()
    monkeypatch.setenv("DATABASE_URL", url)
    with db.pool().connection() as conn:
        # On TimescaleDB a background refresh can hold run_log while the schema is dropped.
        for attempt in range(5):
            try:
                conn.execute("DROP SCHEMA public CASCADE")
                conn.execute("CREATE SCHEMA public")
                conn.commit()
                break
            except psycopg.errors.DeadlockDetected:
                conn.rollback()
                if attempt == 4:
                    raise
    db.migrate(verbose=False)
    yield db
    db.close()
