"""Shared fixtures. `pg` gives a freshly migrated Postgres database.

Database tests need TEST_DATABASE_URL pointing at a throwaway database whose role is not a
superuser (superusers bypass row-level security). They skip when it is unset. CI runs a Postgres
service container for them.
"""

import os

import pytest

from forkward import db


@pytest.fixture
def pg(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    db.close()
    monkeypatch.setenv("DATABASE_URL", url)
    with db.pool().connection() as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        conn.commit()
    db.migrate(verbose=False)
    yield db
    db.close()
