"""Solana anchoring: the hand-built Memo transaction, the decision digest, and verification
against the chain (with the RPC mocked; the database test skips without TEST_DATABASE_URL)."""

import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from forkward import db, solana, workflows

BLOCKHASH = solana.b58encode(bytes(range(32)))


def test_base58_round_trips_and_knows_the_memo_program():
    for raw in (b"", b"\0\0abc", bytes(range(32)), b"\xff" * 64):
        assert solana.b58decode(solana.b58encode(raw)) == raw
    assert solana.b58encode(b"\0\0\x01") == "112"
    assert len(solana.b58decode(solana.MEMO_PROGRAM)) == 32


def test_keypairs_use_the_solana_cli_format_and_are_never_overwritten(tmp_path):
    path = tmp_path / "id.json"
    addr = solana.keygen(path)
    raw = json.loads(path.read_text())
    assert len(raw) == 64 and path.stat().st_mode & 0o777 == 0o600
    _, pub = solana.load_keypair(path)
    assert solana.b58encode(pub) == addr and bytes(raw[32:]) == pub
    with pytest.raises(FileExistsError):
        solana.keygen(path)
    path.write_text(json.dumps(raw[:32] + [0] * 32))
    with pytest.raises(ValueError, match="does not match"):
        solana.load_keypair(path)


def test_memo_transaction_is_a_signed_one_instruction_legacy_message(tmp_path):
    solana.keygen(tmp_path / "id.json")
    sk, pub = solana.load_keypair(tmp_path / "id.json")
    tx = solana.memo_transaction(sk, pub, BLOCKHASH, b"forkward:v1:job:abc")
    assert tx[0] == 1
    sig, msg = tx[1:65], tx[65:]
    Ed25519PublicKey.from_public_bytes(pub).verify(sig, msg)
    assert msg[:4] == bytes([1, 0, 1, 2])
    assert msg[4:36] == pub and msg[36:68] == solana.b58decode(solana.MEMO_PROGRAM)
    assert msg[68:100] == solana.b58decode(BLOCKHASH)
    assert msg[100:] == bytes([1, 1, 0, 19]) + b"forkward:v1:job:abc"


def test_compact_lengths_match_solana_shortvec():
    assert solana._compact(0) == b"\x00"
    assert solana._compact(127) == b"\x7f"
    assert solana._compact(128) == b"\x80\x01"
    assert solana._compact(16384) == b"\x80\x80\x01"


def hunk(file, idx, resolution, decision="auto", confidence=0.9):
    return {
        "file": file,
        "idx": idx,
        "conflict_sha256": f"c-{file}-{idx}",
        "decision": decision,
        "confidence": confidence,
        "resolution": resolution,
        "approved_by": "someone",
    }


def test_digest_ignores_order_and_review_columns_but_not_decisions():
    a, b = hunk("a.c", 0, "x"), hunk("b.c", 0, "y", "review", 0.4)
    d = solana.decisions_digest("j1", "acme/fork", "c0ffee", [a, b])
    assert d == solana.decisions_digest(
        "j1", "acme/fork", "c0ffee", [b, {**a, "approved_by": None}]
    )
    assert d != solana.decisions_digest("j1", "acme/fork", "c0ffee", [hunk("a.c", 0, "z"), b])
    assert d != solana.decisions_digest(
        "j1", "acme/fork", "c0ffee", [{**a, "decision": "review"}, b]
    )
    assert d != solana.decisions_digest("j2", "acme/fork", "c0ffee", [a, b])


def test_send_memo_submits_and_waits_for_confirmation(monkeypatch, tmp_path):
    solana.keygen(tmp_path / "id.json")
    monkeypatch.setenv("SOLANA_KEYPAIR", str(tmp_path / "id.json"))
    monkeypatch.delenv("SOLANA_RPC_URL", raising=False)
    monkeypatch.setattr(solana.time, "sleep", lambda s: None)
    sent, polls = [], []

    def handler(request):
        body = json.loads(request.content)
        if body["method"] == "getLatestBlockhash":
            result = {"value": {"blockhash": BLOCKHASH}}
        elif body["method"] == "sendTransaction":
            sent.append(base64.b64decode(body["params"][0]))
            result = "SiG"
        else:
            polls.append(1)
            result = {
                "value": [
                    None if len(polls) < 2 else {"confirmationStatus": "confirmed", "err": None}
                ]
            }
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        assert solana.send_memo("forkward:v1:j:d", client=c) == "SiG"
    assert sent[0].endswith(b"forkward:v1:j:d") and len(polls) == 2
    assert solana.explorer_url("SiG") == "https://explorer.solana.com/tx/SiG?cluster=devnet"


def test_fetch_memo_reads_the_memo_instruction():
    def handler(request):
        tx = {
            "transaction": {
                "message": {
                    "accountKeys": ["Payer111", solana.MEMO_PROGRAM],
                    "instructions": [
                        {"programIdIndex": 1, "accounts": [], "data": solana.b58encode(b"hello")}
                    ],
                }
            }
        }
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": tx})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        assert solana.fetch_memo("SiG", client=c) == "hello"


def test_anchored_jobs_verify_until_a_decision_is_changed(pg, monkeypatch):
    from forkward import billing

    tid = billing.create_tenant("acme", "team")
    job_id = workflows.create_job(tid, "acme/fork", "up/r@main")
    job = workflows.load_job(job_id)
    res = {
        "pr_url": "https://github.com/acme/fork/pull/5",
        "head_sha": "c0ffee",
        "hunks": [
            {
                "file": "a.c",
                "hunk_id": "a.c#1",
                "resolution": "x",
                "confidence": 0.95,
                "gate": "auto_apply",
                "reason": "won the vote",
                "key": "k1",
            },
            {
                "file": "b.c",
                "hunk_id": "b.c#1",
                "resolution": "y",
                "confidence": 0.4,
                "gate": "needs_review",
                "reason": "disagreed",
                "key": "k2",
            },
        ],
    }
    workflows.record_result(job, res)
    chain = {}
    monkeypatch.setenv("SOLANA_KEYPAIR", "unused")
    monkeypatch.setattr(solana, "send_memo", lambda memo: chain.setdefault("SiG", memo) and "SiG")
    monkeypatch.setattr(solana, "fetch_memo", lambda sig, client=None: chain.get(sig))

    a = workflows.anchor_decisions(job, "c0ffee")
    assert chain["SiG"] == solana.memo_for(job_id, a["digest"])
    v = solana.verify_job(tid, job_id)
    assert v["ok"] and v["on_chain"] == chain["SiG"]
    with db.tenant(tid) as cur:
        cur.execute("SELECT action, detail FROM audit_events ORDER BY ts")
        rows = cur.fetchall()
        cur.execute("SELECT message FROM job_events WHERE job_id = %s ORDER BY id", (job_id,))
        events = [r["message"] for r in cur.fetchall()]
    assert rows[-1]["action"] == "decisions.anchored" and rows[-1]["detail"]["job_id"] == job_id
    assert "Anchored the decisions on Solana devnet" in events

    # Someone rewrites a recorded resolution after the fact: the digest no longer matches.
    with db.tenant(tid) as cur:
        cur.execute("UPDATE hunks SET resolution = 'evil' WHERE file = 'a.c'")
    assert not solana.verify_job(tid, job_id)["ok"]
    assert solana.verify_job(tid, "00000000-0000-0000-0000-000000000000")["ok"] is False


def test_a_chain_outage_never_fails_the_sync(pg, monkeypatch):
    from forkward import billing

    tid = billing.create_tenant("acme", "team")
    job = workflows.load_job(workflows.create_job(tid, "acme/fork", "up/r@main"))
    monkeypatch.delenv("SOLANA_KEYPAIR", raising=False)
    assert workflows.anchor_decisions(job, None) is None
    monkeypatch.setenv("SOLANA_KEYPAIR", "unused")

    def down(memo):
        raise httpx.ConnectError("devnet is down")

    monkeypatch.setattr(solana, "send_memo", down)
    assert workflows.anchor_decisions(job, None) is None
    with db.tenant(tid) as cur:
        cur.execute("SELECT kind, message FROM job_events WHERE job_id = %s", (job["id"],))
        assert any(
            r["kind"] == "warning" and "devnet is down" in r["message"] for r in cur.fetchall()
        )


def test_outcomes_are_anchored_after_decisions_and_verified_together(pg, monkeypatch):
    from forkward import billing

    tid = billing.create_tenant("acme", "team")
    job_id = workflows.create_job(tid, "acme/fork", "up/r@main")
    job = workflows.load_job(job_id)
    res = {
        "pr_url": "https://github.com/acme/fork/pull/5",
        "head_sha": "c0ffee",
        "hunks": [
            {
                "file": "a.c",
                "hunk_id": "a.c#1",
                "resolution": "x",
                "confidence": 0.95,
                "gate": "needs_review",
                "reason": "uncertified",
                "key": "k1",
            },
            {
                "file": "b.c",
                "hunk_id": "b.c#1",
                "resolution": "y",
                "confidence": 0.4,
                "gate": "needs_review",
                "reason": "disagreed",
                "key": "k2",
            },
        ],
    }
    workflows.record_result(job, res)
    chain, n = {}, iter(range(100))
    monkeypatch.setenv("SOLANA_KEYPAIR", "unused")

    def send(memo):
        sig = f"SiG{next(n)}"
        chain[sig] = memo
        return sig

    monkeypatch.setattr(solana, "send_memo", send)
    monkeypatch.setattr(solana, "fetch_memo", lambda sig, client=None: chain.get(sig))

    # No prediction on record yet, so there is no outcome to anchor.
    assert solana.anchor_outcome(job, merged=True) is None
    decided = workflows.anchor_decisions(job, "c0ffee")
    with db.tenant(tid) as cur:
        cur.execute(
            "UPDATE hunks SET review = 'accepted', final_resolution = resolution WHERE file = 'a.c'"
        )
        cur.execute(
            "UPDATE hunks SET review = 'accepted', final_resolution = 'y2' WHERE file = 'b.c'"
        )
    out = workflows.anchor_outcome(job, True)
    assert out["decisions"] == decided["signature"] and out["merged"] is True
    assert chain[out["signature"]] == solana.outcome_memo_for(job_id, out["digest"])

    v = solana.verify_job(tid, job_id)
    assert v["ok"] and v["outcome"]["ok"] and v["outcome"]["merged"] is True
    with db.tenant(tid) as cur:
        cur.execute("SELECT message FROM job_events WHERE job_id = %s ORDER BY id", (job_id,))
        assert "Anchored the review outcome on Solana devnet" in [
            r["message"] for r in cur.fetchall()
        ]

    # A reviewed resolution rewritten after the merge no longer matches the outcome on chain.
    with db.tenant(tid) as cur:
        cur.execute("UPDATE hunks SET final_resolution = 'evil' WHERE file = 'b.c'")
    v = solana.verify_job(tid, job_id)
    assert not v["ok"] and not v["outcome"]["ok"]
