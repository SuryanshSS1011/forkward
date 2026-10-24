"""Postgres access: one connection pool per process, ordered SQL migrations, and tenant scoping.

Every customer-owned table has forced row-level security (see migrations/001_core.sql), so each
transaction must say whose rows it may touch:

    with db.tenant("t_123") as cur:      # sees only tenant t_123
        cur.execute("SELECT ... FROM jobs")
    with db.system() as cur:             # workers, the CLI and API-key lookup
        ...

Both open one transaction, set the scope with SET LOCAL semantics, and commit on exit. The
connection string comes from DATABASE_URL (Neon in production, any Postgres 15+ locally).
"""

from __future__ import annotations

import atexit
import os
import re
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
_POOLS: dict[str, object] = {}
_ROLE_OK: set[str] = set()
_LOCK = threading.Lock()


class DatabaseError(RuntimeError):
    pass


def url() -> str | None:
    return os.environ.get("DATABASE_URL") or None


def configured() -> bool:
    return url() is not None


def pool():
    """The process-wide pool for DATABASE_URL. Opened lazily, so importing this module never
    touches the network."""
    u = url()
    if not u:
        raise DatabaseError("DATABASE_URL is not set")
    with _LOCK:
        p = _POOLS.get(u)
        if p is None:
            from psycopg_pool import ConnectionPool

            p = ConnectionPool(
                u,
                min_size=0,
                max_size=int(os.environ.get("DB_POOL_MAX", "8")),
                kwargs={"application_name": "forkward", "connect_timeout": 10},
                open=True,
            )
            if not _POOLS:
                atexit.register(close)
            _POOLS[u] = p
        return p


def close() -> None:
    with _LOCK:
        for p in _POOLS.values():
            p.close()
        _POOLS.clear()
        _ROLE_OK.clear()


def check_role(conn) -> None:
    """Refuse a connection whose role skips row-level security (a superuser, or BYPASSRLS as some
    hosted Postgres owner roles have): every tenant would silently see every other tenant's rows."""
    row = conn.execute(
        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    if row and row[0]:
        raise DatabaseError(
            "the DATABASE_URL role bypasses row-level security (superuser or BYPASSRLS); connect "
            "as a role with neither so tenant isolation holds"
        )


@contextmanager
def _scoped(settings: dict[str, str]) -> Iterator:
    from psycopg.rows import dict_row

    u = url()
    with pool().connection() as conn, conn.transaction():
        if u not in _ROLE_OK:
            check_role(conn)
            _ROLE_OK.add(u)
        cur = conn.cursor(row_factory=dict_row)
        for k, v in settings.items():
            cur.execute("SELECT set_config(%s, %s, true)", (k, v))
        yield cur


_TENANT = re.compile(r"^[A-Za-z0-9_:.-]{1,128}$")


@contextmanager
def tenant(tenant_id: str) -> Iterator:
    """A transaction that can read and write only `tenant_id`'s rows."""
    if not tenant_id or not _TENANT.match(tenant_id):
        raise DatabaseError("invalid tenant id")
    with _scoped({"app.tenant_id": tenant_id, "app.role": "tenant"}) as cur:
        yield cur


@contextmanager
def system() -> Iterator:
    """A transaction that may touch every tenant. For workers, the CLI and key lookup only."""
    with _scoped({"app.role": "system", "app.tenant_id": ""}) as cur:
        yield cur


def migrate(verbose: bool = True) -> list[str]:
    """Apply migrations/NNN_*.sql in order, each in its own transaction, once."""
    with pool().connection() as conn:
        with conn.transaction():
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
        applied = []
        for f in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
            if f.name in done:
                continue
            with conn.transaction():
                # Serialize concurrent deploys on the same database.
                conn.execute("SELECT pg_advisory_xact_lock(7347001)")
                if conn.execute(
                    "SELECT 1 FROM schema_migrations WHERE name = %s", (f.name,)
                ).fetchone():
                    continue
                conn.execute(f.read_text())
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))
            applied.append(f.name)
            if verbose:
                print(f"applied {f.name}", file=sys.stderr)
        return applied


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="forkward db")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply pending migrations")
    sub.add_parser("status", help="list applied migrations")
    a = p.parse_args(argv)
    if a.cmd == "migrate":
        applied = migrate()
        print(f"{len(applied)} migration(s) applied")
    else:
        with system() as cur:
            cur.execute("SELECT name, applied_at FROM schema_migrations ORDER BY name")
            for r in cur.fetchall():
                print(f"{r['name']}  {r['applied_at']:%Y-%m-%d %H:%M}")
    return 0
