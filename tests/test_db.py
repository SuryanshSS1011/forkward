"""Migrations and the run-log mirror on a real Postgres (skipped without TEST_DATABASE_URL)."""

from forkward import db, store


def test_migrations_apply_once(pg):
    assert db.migrate(verbose=False) == []
    with db.system() as cur:
        cur.execute("SELECT name FROM schema_migrations")
        assert {"001_core.sql", "002_better_auth.sql"} <= {r["name"] for r in cur.fetchall()}


def test_better_auth_schema(pg):
    """002 creates the tables the web app's Better Auth needs, outside row-level security."""
    with db.system() as cur:
        cur.execute(
            "SELECT c.relname, c.relrowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r'"
        )
        tables = {r["relname"]: r["relrowsecurity"] for r in cur.fetchall()}
    for t in (
        "user",
        "session",
        "account",
        "verification",
        "jwks",
        "oauthClient",
        "oauthAccessToken",
        "oauthRefreshToken",
        "oauthConsent",
    ):
        assert t in tables, t
        assert tables[t] is False
    with db.system() as cur:
        cur.execute(
            'INSERT INTO "user" (id, name, email, "emailVerified") VALUES (%s, %s, %s, true)',
            ("u1", "Ada", "ada@example.com"),
        )
        cur.execute('SELECT email FROM "user" WHERE id = %s', ("u1",))
        assert cur.fetchone()["email"] == "ada@example.com"


def test_runs_are_mirrored_without_prompts(pg, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "RUNS", tmp_path)
    store._PG.clear()
    s = store.Store("r-test", batch_size=2)
    assert s.mirrored
    s.add("attempts", {"hunk": "a#1", "prompt": "secret prompt", "response": "x", "ok": True})
    s.event("resolver", "start", hunk="a#1")
    s.close()
    with db.system() as cur:
        cur.execute("SELECT collection, doc FROM run_log WHERE run_id = 'r-test'")
        rows = {r["collection"]: r["doc"] for r in cur.fetchall()}
    assert set(rows) == {"attempts", "events"}
    assert "prompt" not in rows["attempts"] and rows["attempts"]["ok"] is True
    assert "secret prompt" in (tmp_path / "r-test" / "attempts.jsonl").read_text()
