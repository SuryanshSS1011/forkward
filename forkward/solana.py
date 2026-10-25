"""Tamper evidence for sync decisions on Solana devnet.

After a sync opens its pull request, a SHA-256 over the job's hunks (conflict hash, gate decision,
confidence and chosen resolution) is written to the chain with the Memo program, and the signature
lands in audit_events. When the pull request is merged or closed, a second memo anchors the
reviewer's verdict on every hunk and links back to the first. So each prediction is on record before
its outcome exists, and neither can be edited later without the digests no longer matching. Anyone
can recompute both from the database and compare them with the chain. Optional: without
SOLANA_KEYPAIR nothing is anchored.

The transaction is a one-instruction legacy message, serialized here so no Solana SDK is needed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

MEMO_PROGRAM = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
DEVNET = "https://api.devnet.solana.com"
MEMO_PREFIX = "forkward:v1"
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out


def b58decode(s: str) -> bytes:
    n = 0
    for c in s:
        n = n * 58 + _B58.index(c)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(s) - len(s.lstrip("1"))) + body


def rpc_url() -> str:
    return os.environ.get("SOLANA_RPC_URL") or DEVNET


def cluster() -> str:
    url = rpc_url()
    return next((c for c in ("devnet", "testnet", "mainnet") if c in url), "custom")


def explorer_url(signature: str) -> str:
    c = cluster()
    return f"https://explorer.solana.com/tx/{signature}" + (
        f"?cluster={c}" if c in ("devnet", "testnet") else ""
    )


def enabled() -> bool:
    return bool(os.environ.get("SOLANA_KEYPAIR"))


def keygen(path: Path) -> str:
    """Write a new keypair in solana-cli's format (64 bytes: seed then public key) and return
    its address. Refuses to overwrite an existing file."""
    sk = Ed25519PrivateKey.generate()
    seed = sk.private_bytes_raw()
    pub = sk.public_key().public_bytes_raw()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(list(seed + pub), f)
    return b58encode(pub)


def load_keypair(path: Path | None = None) -> tuple[Ed25519PrivateKey, bytes]:
    raw = bytes(json.loads(Path(path or os.environ["SOLANA_KEYPAIR"]).expanduser().read_text()))
    if len(raw) != 64:
        raise ValueError("a Solana keypair file holds 64 bytes")
    sk = Ed25519PrivateKey.from_private_bytes(raw[:32])
    pub = sk.public_key().public_bytes_raw()
    if pub != raw[32:]:
        raise ValueError("the keypair's public key does not match its seed")
    return sk, pub


def _compact(n: int) -> bytes:
    """Solana's shortvec length prefix."""
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def memo_transaction(sk: Ed25519PrivateKey, payer: bytes, blockhash: str, memo: bytes) -> bytes:
    """A signed legacy transaction with one Memo instruction, paid for by `payer`. Memo v2 needs
    no accounts, so the payer is the only signer and the program the only read-only key."""
    message = (
        bytes([1, 0, 1])  # one signature, no read-only signers, one read-only unsigned key
        + _compact(2)
        + payer
        + b58decode(MEMO_PROGRAM)
        + b58decode(blockhash)
        + _compact(1)
        + bytes([1])  # program id index
        + _compact(0)
        + _compact(len(memo))
        + memo
    )
    return _compact(1) + sk.sign(message) + message


def _rpc(client: httpx.Client, method: str, params: list) -> dict | list | str | int | None:
    r = client.post(rpc_url(), json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(f"{method}: {data['error'].get('message', data['error'])}")
    return data.get("result")


def send_memo(memo: str, client: httpx.Client | None = None, wait_s: float = 45.0) -> str:
    """Submit `memo` and wait until it is confirmed. Returns the transaction signature."""
    sk, payer = load_keypair()
    own = client is None
    client = client or httpx.Client(timeout=20.0)
    try:
        blockhash = _rpc(client, "getLatestBlockhash", [{"commitment": "confirmed"}])["value"][
            "blockhash"
        ]
        tx = memo_transaction(sk, payer, blockhash, memo.encode())
        sig = _rpc(
            client,
            "sendTransaction",
            [
                base64.b64encode(tx).decode(),
                {"encoding": "base64", "preflightCommitment": "confirmed"},
            ],
        )
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            st = _rpc(client, "getSignatureStatuses", [[sig]])["value"][0]
            if st and st.get("err"):
                raise RuntimeError(f"transaction {sig} failed: {st['err']}")
            if st and st.get("confirmationStatus") in ("confirmed", "finalized"):
                return sig
            time.sleep(1.0)
        raise TimeoutError(f"transaction {sig} was not confirmed within {wait_s:.0f} s")
    finally:
        if own:
            client.close()


def fetch_memo(signature: str, client: httpx.Client | None = None) -> str | None:
    """The memo text written by `signature`, or None if it has no Memo instruction."""
    own = client is None
    client = client or httpx.Client(timeout=20.0)
    try:
        tx = _rpc(
            client,
            "getTransaction",
            [
                signature,
                {
                    "encoding": "json",
                    "commitment": "confirmed",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        )
    finally:
        if own:
            client.close()
    if not tx:
        return None
    msg = tx["transaction"]["message"]
    keys = msg["accountKeys"]
    for ix in msg["instructions"]:
        if keys[ix["programIdIndex"]] == MEMO_PROGRAM:
            return b58decode(ix["data"]).decode()
    return None


def airdrop(sol: float = 1.0, client: httpx.Client | None = None) -> str:
    """Devnet only: fund the configured keypair."""
    if cluster() != "devnet":
        raise RuntimeError("airdrops exist only on devnet")
    _, payer = load_keypair()
    own = client is None
    client = client or httpx.Client(timeout=20.0)
    try:
        return _rpc(client, "requestAirdrop", [b58encode(payer), int(sol * 1_000_000_000)])
    finally:
        if own:
            client.close()


def decisions_digest(job_id: str, repo: str, head_sha: str | None, hunks: list[dict]) -> str:
    """SHA-256 over the decisions as they were made. Only columns written once, when the hunk is
    recorded, go in, so a later review does not change the digest."""
    rows = sorted(
        (
            {
                "file": h["file"],
                "idx": h["idx"],
                "conflict": h["conflict_sha256"],
                "decision": h["decision"],
                "confidence": None if h["confidence"] is None else round(h["confidence"], 6),
                "resolution": hashlib.sha256((h["resolution"] or "").encode()).hexdigest(),
            }
            for h in hunks
        ),
        key=lambda r: (r["file"], r["idx"]),
    )
    doc = {"job": job_id, "repo": repo, "head": head_sha, "hunks": rows}
    return hashlib.sha256(
        json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def memo_for(job_id: str, digest: str) -> str:
    return f"{MEMO_PREFIX}:{job_id}:{digest}"


def outcome_memo_for(job_id: str, digest: str) -> str:
    return f"{MEMO_PREFIX}:outcome:{job_id}:{digest}"


def outcome_digest(job_id: str, merged: bool, decision_signature: str, reviews: list[dict]) -> str:
    """SHA-256 over what the reviewer kept: each hunk's verdict and final text, whether the pull
    request merged, and the decision anchor this outcome answers."""
    rows = sorted(
        (
            {
                "file": r["file"],
                "idx": r["idx"],
                "review": r["review"],
                "final": hashlib.sha256((r["final_resolution"] or "").encode()).hexdigest(),
            }
            for r in reviews
        ),
        key=lambda r: (r["file"], r["idx"]),
    )
    doc = {"job": job_id, "merged": merged, "decisions": decision_signature, "hunks": rows}
    return hashlib.sha256(
        json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _job_hunks(cur, job_id: str) -> list[dict]:
    cur.execute(
        "SELECT file, idx, conflict_sha256, decision, confidence, resolution FROM hunks "
        "WHERE job_id = %s",
        (job_id,),
    )
    return cur.fetchall()


def _job_reviews(cur, job_id: str) -> list[dict]:
    cur.execute(
        "SELECT file, idx, review, final_resolution FROM hunks WHERE job_id = %s", (job_id,)
    )
    return cur.fetchall()


def _latest_anchor(cur, action: str, job_id: str) -> dict | None:
    cur.execute(
        "SELECT detail FROM audit_events WHERE action = %s AND detail->>'job_id' = %s "
        "ORDER BY ts DESC LIMIT 1",
        (action, job_id),
    )
    row = cur.fetchone()
    return row["detail"] if row else None


def anchor_outcome(job: dict, merged: bool) -> dict | None:
    """Anchor the reviewer's verdict once the pull request is merged or closed. Returns None when
    the job's decisions were never anchored, since there is no prediction to answer."""
    from forkward import db

    job_id = str(job["id"])
    with db.tenant(job["tenant_id"]) as cur:
        decisions = _latest_anchor(cur, "decisions.anchored", job_id)
        if not decisions:
            return None
        digest = outcome_digest(job_id, merged, decisions["signature"], _job_reviews(cur, job_id))
    sig = send_memo(outcome_memo_for(job_id, digest))
    detail = {
        "job_id": job_id,
        "digest": digest,
        "merged": merged,
        "decisions": decisions["signature"],
        "signature": sig,
        "cluster": cluster(),
        "explorer": explorer_url(sig),
    }
    with db.tenant(job["tenant_id"]) as cur:
        cur.execute(
            "INSERT INTO audit_events (tenant_id, actor, action, subject, detail) "
            "VALUES (%s, 'forkward-bot', 'outcome.anchored', %s, %s)",
            (job["tenant_id"], job["repo"], json.dumps(detail)),
        )
    return detail


def anchor_job(job: dict, head_sha: str | None) -> dict:
    """Anchor a finished job's decisions and record the signature as an audit event."""
    from forkward import db

    with db.tenant(job["tenant_id"]) as cur:
        digest = decisions_digest(str(job["id"]), job["repo"], head_sha, _job_hunks(cur, job["id"]))
    sig = send_memo(memo_for(str(job["id"]), digest))
    detail = {
        "job_id": str(job["id"]),
        "digest": digest,
        "signature": sig,
        "cluster": cluster(),
        "explorer": explorer_url(sig),
        "head_sha": head_sha,
    }
    with db.tenant(job["tenant_id"]) as cur:
        cur.execute(
            "INSERT INTO audit_events (tenant_id, actor, action, subject, detail) "
            "VALUES (%s, 'forkward-bot', 'decisions.anchored', %s, %s)",
            (job["tenant_id"], job["repo"], json.dumps(detail)),
        )
    return detail


def verify_job(tenant_id: str, job_id: str, client: httpx.Client | None = None) -> dict:
    """Recompute the job's decision digest, and its outcome digest once there is one, from the
    database and compare each with its memo on chain."""
    from forkward import db

    with db.tenant(tenant_id) as cur:
        cur.execute("SELECT repo FROM jobs WHERE id = %s", (job_id,))
        job = cur.fetchone()
        if not job:
            return {"ok": False, "reason": "no such job"}
        anchor = _latest_anchor(cur, "decisions.anchored", job_id)
        outcome = _latest_anchor(cur, "outcome.anchored", job_id)
        hunks = _job_hunks(cur, job_id)
        reviews = _job_reviews(cur, job_id) if outcome else []
    if not anchor:
        return {"ok": False, "reason": "this job was never anchored"}
    digest = decisions_digest(job_id, job["repo"], anchor.get("head_sha"), hunks)
    on_chain = fetch_memo(anchor["signature"], client)
    res = {
        "ok": anchor["digest"] == digest and on_chain == memo_for(job_id, digest),
        "digest": digest,
        "recorded": anchor["digest"],
        "on_chain": on_chain,
        "signature": anchor["signature"],
        "explorer": explorer_url(anchor["signature"]),
    }
    if outcome:
        o_digest = outcome_digest(job_id, outcome["merged"], outcome["decisions"], reviews)
        o_chain = fetch_memo(outcome["signature"], client)
        res["outcome"] = {
            "ok": outcome["digest"] == o_digest
            and o_chain == outcome_memo_for(job_id, o_digest)
            and outcome["decisions"] == anchor["signature"],
            "merged": outcome["merged"],
            "digest": o_digest,
            "signature": outcome["signature"],
            "explorer": explorer_url(outcome["signature"]),
        }
        res["ok"] = res["ok"] and res["outcome"]["ok"]
    return res


def main(argv: list[str]) -> int:
    import argparse

    from forkward import llm

    llm.load_env()
    p = argparse.ArgumentParser(prog="python -m forkward solana")
    sub = p.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen", help="create the anchoring keypair")
    k.add_argument("path", type=Path)
    sub.add_parser("airdrop", help="fund SOLANA_KEYPAIR on devnet")
    m = sub.add_parser("memo", help="anchor an arbitrary memo (a smoke test)")
    m.add_argument("text")
    v = sub.add_parser("verify", help="check a job's decisions against the chain")
    v.add_argument("tenant_id")
    v.add_argument("job_id")
    a = p.parse_args(argv)
    if a.cmd == "keygen":
        print(keygen(a.path))
    elif a.cmd == "airdrop":
        print(airdrop())
    elif a.cmd == "memo":
        sig = send_memo(a.text)
        print(json.dumps({"signature": sig, "explorer": explorer_url(sig)}, indent=1))
    else:
        res = verify_job(a.tenant_id, a.job_id)
        print(json.dumps(res, indent=1))
        return 0 if res["ok"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
