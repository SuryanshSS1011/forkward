"""API keys, plans, quotas and usage metering for the hosted MCP server, on Postgres.

A tenant (a customer organization) owns API keys and is on one plan. Keys are stored as SHA-256
hashes. Before any model call a sync reserves units atomically in `usage_quota`, then settles the
units it actually used in `usage_events`, which is both the ledger and the outbox that
`report_to_stripe` drains into a Stripe Billing meter. The unit of billing is a conflict hunk
Forkward resolved, plus one unit per sync PR and per build fix, so a customer pays for merge work
done rather than for tokens.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime

from forkward import db, llm

# A monthly fee covers `included_units`, each unit past that costs `price_per_unit_usd`, and calls
# stop at `monthly_units`. Stripe bills the fee and the overage from the meter; these numbers
# must match the Stripe prices, and the plan a tenant is on is written by the Stripe webhook.
PLANS: dict[str, dict] = {
    "trial": {
        "monthly_units": 500,
        "included_units": 500,
        "fee_usd": 0.0,
        "price_per_unit_usd": 0.0,
    },
    "team": {
        "monthly_units": 5000,
        "included_units": 2000,
        "fee_usd": 99.0,
        "price_per_unit_usd": 0.05,
    },
    "enterprise": {
        "monthly_units": 100000,
        "included_units": 50000,
        "fee_usd": 1499.0,
        "price_per_unit_usd": 0.03,
    },
}


# Units charged per resolved hunk, by model: roughly the inference cost relative to Flash.
MODEL_UNITS = {
    "deepseek:deepseek-chat": 1,
    "gemini:gemini-2.5-flash": 1,
    "vultr:deepseek-v4.1-flash": 1,
    "anthropic:claude-haiku-4-5": 2,
    "anthropic:claude-sonnet-5": 4,
    "anthropic:claude-opus-5": 8,
}


MAX_UNITS = max(MODEL_UNITS.values())
STRIPE_METER_EVENT = os.environ.get("STRIPE_METER_EVENT", "forkward_hunks")


def price(model_spec: str | None) -> int:
    """Units per hunk. The server default and the configured Luminal generator cost 1. Any other
    model id, including unlisted variants of a listed one, is charged at the highest rate, so a
    caller can never pay a cheap rate for an expensive model."""
    if not model_spec:
        return 1
    if model_spec in MODEL_UNITS:
        return MODEL_UNITS[model_spec]
    default = os.environ.get("GENERATOR_MODEL")
    if default and model_spec == f"luminal:{default}":
        return 1
    return MAX_UNITS


def units_for(model_spec: str | None, hunks: int) -> int:
    return hunks * price(model_spec)


class BillingError(RuntimeError):
    pass


@dataclass(frozen=True)
class Customer:
    key_id: str
    name: str
    plan: str
    tenant_id: str = ""


def _ready() -> None:
    llm.load_env()
    if not db.configured():
        raise BillingError("metering needs DATABASE_URL")


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _month_start() -> date:
    return datetime.now(UTC).date().replace(day=1)


def new_tenant_id() -> str:
    return "t_" + secrets.token_hex(8)


def create_tenant(name: str, plan: str = "trial", tenant_id: str | None = None) -> str:
    if plan not in PLANS:
        raise BillingError(f"unknown plan {plan!r}; choose one of {sorted(PLANS)}")
    _ready()
    tid = tenant_id or new_tenant_id()
    with db.system() as cur:
        cur.execute(
            "INSERT INTO tenants (id, name, plan) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (tid, name, plan),
        )
    return tid


def set_plan(tenant_id: str, plan: str) -> None:
    if plan not in PLANS:
        raise BillingError(f"unknown plan {plan!r}")
    _ready()
    with db.system() as cur:
        cur.execute(
            "UPDATE tenants SET plan = %s, updated_at = now() WHERE id = %s", (plan, tenant_id)
        )


def create_key(name: str, plan: str = "trial", tenant_id: str | None = None) -> str:
    """Returns the plaintext key once. Only its hash is stored. Without `tenant_id` a new tenant
    named `name` is created on `plan`."""
    tid = tenant_id or create_tenant(name, plan)
    key = "fw_" + secrets.token_urlsafe(24)
    with db.tenant(tid) as cur:
        cur.execute("SELECT 1 FROM tenants WHERE id = %s", (tid,))
        if cur.fetchone() is None:
            raise BillingError(f"no tenant {tid!r}")
        cur.execute(
            "INSERT INTO api_keys (tenant_id, key_hash, key_id, name) VALUES (%s, %s, %s, %s)",
            (tid, _hash(key), key[:7] + "..." + key[-4:], name),
        )
    return key


def revoke(key_id: str) -> bool:
    _ready()
    with db.system() as cur:
        cur.execute(
            "UPDATE api_keys SET active = false, revoked_at = now() WHERE key_id = %s AND active",
            (key_id,),
        )
        return cur.rowcount == 1


def authenticate(key: str | None) -> Customer:
    if not key:
        raise BillingError("missing API key: send `Authorization: Bearer <key>`")
    try:
        _ready()
        with db.system() as cur:
            cur.execute(
                "UPDATE api_keys k SET last_used_at = now() FROM tenants t "
                "WHERE k.key_hash = %s AND k.active AND t.id = k.tenant_id "
                "RETURNING k.key_id, k.name, t.plan, t.id AS tenant_id",
                (_hash(key),),
            )
            row = cur.fetchone()
    except BillingError:
        raise
    except Exception as e:  # noqa: BLE001 - never leak backend hostnames to callers
        print(f"[billing] key lookup failed: {e}", file=sys.stderr)
        raise BillingError("metering is temporarily unavailable; try again shortly") from None
    if not row:
        raise BillingError("invalid or revoked API key")
    return Customer(row["key_id"], row["name"], row["plan"], row["tenant_id"])


def used_this_month(c: Customer) -> int:
    with db.tenant(c.tenant_id) as cur:
        cur.execute(
            "SELECT coalesce(sum(units), 0) AS units FROM usage_events "
            "WHERE tenant_id = %s AND ts >= %s",
            (c.tenant_id, _month_start()),
        )
        return int(cur.fetchone()["units"])


def check_quota(c: Customer, needed: int = 1) -> int:
    """Raises when the call would exceed the plan. Returns units left before the call."""
    with db.tenant(c.tenant_id) as cur:
        cur.execute(
            "SELECT used FROM usage_quota WHERE tenant_id = %s AND month = %s",
            (c.tenant_id, _month_start()),
        )
        row = cur.fetchone()
    used = row["used"] if row else used_this_month(c)
    left = PLANS[c.plan]["monthly_units"] - used
    if left < needed:
        raise BillingError(
            f"monthly quota reached for plan {c.plan!r} ({PLANS[c.plan]['monthly_units']} units)"
        )
    return left


@dataclass
class Reservation:
    customer: Customer
    month: date
    units: int


def reserve(c: Customer, units: int) -> Reservation:
    """Atomically take `units` from this month's allowance before any model work, or raise.
    The conditional UPDATE runs under the row lock, so parallel calls cannot all pass the same
    check."""
    month = _month_start()
    if units <= 0:
        return Reservation(c, month, 0)
    limit = PLANS[c.plan]["monthly_units"]
    with db.tenant(c.tenant_id) as cur:
        cur.execute(
            "INSERT INTO usage_quota (tenant_id, month, used) "
            "SELECT %s, %s, coalesce(sum(units), 0) FROM usage_events "
            "WHERE tenant_id = %s AND ts >= %s "
            "ON CONFLICT (tenant_id, month) DO NOTHING",
            (c.tenant_id, month, c.tenant_id, month),
        )
        cur.execute(
            "UPDATE usage_quota SET used = used + %s "
            "WHERE tenant_id = %s AND month = %s AND used + %s <= %s RETURNING used",
            (units, c.tenant_id, month, units, limit),
        )
        if cur.fetchone() is None:
            cur.execute(
                "SELECT used FROM usage_quota WHERE tenant_id = %s AND month = %s",
                (c.tenant_id, month),
            )
            left = limit - cur.fetchone()["used"]
            raise BillingError(
                f"this call needs {units} units but plan {c.plan!r} has {max(0, left)} left "
                "this month"
            )
    return Reservation(c, month, units)


def settle(r: Reservation, tool: str, units: int, **detail) -> None:
    """Charge `units` (at most what was reserved) and return the rest to the allowance, in one
    transaction. Called on success, failure and cancellation alike, so started work is always
    recorded."""
    units = max(0, min(units, r.units))
    c = r.customer
    with db.tenant(c.tenant_id) as cur:
        if units != r.units:
            cur.execute(
                "UPDATE usage_quota SET used = greatest(0, used + %s) "
                "WHERE tenant_id = %s AND month = %s",
                (units - r.units, c.tenant_id, r.month),
            )
        _insert_usage(cur, c, tool, units, reserved=r.units, **detail)


def record(c: Customer, tool: str, units: int, **detail) -> None:
    with db.tenant(c.tenant_id) as cur:
        _insert_usage(cur, c, tool, units, **detail)


def _insert_usage(cur, c: Customer, tool: str, units: int, reserved: int = 0, **detail) -> None:
    repo = detail.pop("repo", None)
    job_id = detail.pop("job_id", None)
    cur.execute(
        "INSERT INTO usage_events (tenant_id, key_id, tool, units, reserved, repo, job_id, detail) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            c.tenant_id,
            c.key_id,
            tool,
            units,
            reserved,
            repo,
            job_id,
            json.dumps(detail, default=str),
        ),
    )


def recent_usage(c: Customer, since: float | None, limit: int) -> list[dict]:
    """Usage rows for the alerts feed, oldest first."""
    with db.tenant(c.tenant_id) as cur:
        cur.execute(
            "SELECT tool, units, repo, ts FROM usage_events WHERE tenant_id = %s "
            "AND (%s::timestamptz IS NULL OR ts > %s::timestamptz) ORDER BY ts DESC LIMIT %s",
            (
                c.tenant_id,
                datetime.fromtimestamp(since, UTC) if since else None,
                datetime.fromtimestamp(since, UTC) if since else None,
                limit,
            ),
        )
        return list(reversed(cur.fetchall()))


def amount_due(plan_name: str, used: int) -> float:
    """The monthly fee plus overage past the included units, as the Stripe prices bill it."""
    plan = PLANS[plan_name]
    return round(
        plan["fee_usd"] + max(0, used - plan["included_units"]) * plan["price_per_unit_usd"], 2
    )


def usage(c: Customer) -> dict:
    with db.tenant(c.tenant_id) as cur:
        cur.execute(
            "SELECT tool, sum(units) AS units, count(*) AS calls FROM usage_events "
            "WHERE tenant_id = %s AND ts >= %s GROUP BY tool",
            (c.tenant_id, _month_start()),
        )
        by_tool = {
            r["tool"]: {"units": int(r["units"]), "calls": int(r["calls"])} for r in cur.fetchall()
        }
    used = sum(v["units"] for v in by_tool.values())
    plan = PLANS[c.plan]
    return {
        "customer": c.name,
        "key_id": c.key_id,
        "plan": c.plan,
        "period_start": datetime.combine(_month_start(), datetime.min.time(), UTC).isoformat(),
        "units_used": used,
        "units_included": plan["monthly_units"],
        "units_left": max(0, plan["monthly_units"] - used),
        "amount_due_usd": amount_due(c.plan, used),
        "by_tool": by_tool,
    }


_STRIPE_ID = re.compile(r"^cus_[A-Za-z0-9]+$")


def report_to_stripe(limit: int = 500, post=None) -> dict:
    """Send unreported usage to the Stripe meter, one meter event per usage row. The usage row id
    is the Stripe idempotency identifier, so a crash between sending and marking never bills
    twice. Rows of tenants without a Stripe customer (trial, CLI keys) are left unreported."""
    _ready()
    key = os.environ.get("STRIPE_SECRET_KEY")
    if post is None:
        if not key:
            raise BillingError("STRIPE_SECRET_KEY is not set")
        import httpx

        client = httpx.Client(timeout=20, auth=(key, ""))

        def post(data: dict) -> int:
            return client.post("https://api.stripe.com/v1/billing/meter_events", data=data)

    sent = failed = 0
    with db.system() as cur:
        cur.execute(
            "SELECT u.id, u.units, u.ts, t.stripe_customer_id FROM usage_events u "
            "JOIN tenants t ON t.id = u.tenant_id "
            "WHERE u.stripe_reported_at IS NULL AND u.units > 0 "
            "AND t.stripe_customer_id IS NOT NULL ORDER BY u.ts LIMIT %s FOR UPDATE OF u SKIP LOCKED",
            (limit,),
        )
        rows = cur.fetchall()
        for r in rows:
            if not _STRIPE_ID.match(r["stripe_customer_id"]):
                failed += 1
                continue
            resp = post(
                {
                    "event_name": STRIPE_METER_EVENT,
                    "identifier": str(r["id"]),
                    "timestamp": int(r["ts"].timestamp()),
                    "payload[stripe_customer_id]": r["stripe_customer_id"],
                    "payload[value]": str(r["units"]),
                }
            )
            status = getattr(resp, "status_code", resp)
            # 400 with resource_already_exists means Stripe already has this identifier.
            body = getattr(resp, "text", "")
            if status < 300 or (status == 400 and "already" in body):
                cur.execute(
                    "UPDATE usage_events SET stripe_reported_at = now() WHERE id = %s", (r["id"],)
                )
                sent += 1
            else:
                failed += 1
                print(
                    f"[billing] stripe meter event failed: {status} {body[:200]}", file=sys.stderr
                )
    return {"sent": sent, "failed": failed}


def main(argv: list[str] | None = None) -> int:
    p = argparse_parser()
    a = p.parse_args(argv)
    if a.cmd == "create":
        print(create_key(a.name, a.plan, a.tenant))
        print("Store this key now; only its hash is kept.", flush=True)
    elif a.cmd == "revoke":
        print("revoked" if revoke(a.key_id) else "no such active key")
    elif a.cmd == "report-usage":
        print(json.dumps(report_to_stripe()))
    else:
        _ready()
        with db.system() as cur:
            cur.execute(
                "SELECT k.key_id, k.name, k.active, k.created_at, t.id AS tenant_id, t.plan "
                "FROM api_keys k JOIN tenants t ON t.id = k.tenant_id ORDER BY k.created_at"
            )
            rows = cur.fetchall()
        for d in rows:
            c = Customer(d["key_id"], d["name"], d["plan"], d["tenant_id"])
            d["units_this_month"] = used_this_month(c)
            print(json.dumps(d, default=str))
    return 0


def argparse_parser():
    import argparse

    p = argparse.ArgumentParser(prog="forkward keys")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("name")
    c.add_argument("--plan", default="trial", choices=sorted(PLANS))
    c.add_argument("--tenant", help="add the key to this tenant instead of creating one")
    r = sub.add_parser("revoke")
    r.add_argument("key_id")
    sub.add_parser("list")
    sub.add_parser("report-usage", help="send unreported usage to the Stripe meter")
    return p
