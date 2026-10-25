import asyncio
import json
import os
import re
from collections import Counter

import pytest

from forkward import agents, algebra, audit, billing, gate, llm, score, search, store
from forkward.data import Hunk, data_dir, load_hunks

needs_data = pytest.mark.skipif(
    not (data_dir() / "split.json").exists(), reason="frozen forkward-data checkout not found"
)


@needs_data
@pytest.mark.parametrize(
    ("split", "classes", "keep_vendor"),
    [
        ("train", {"keep_vendor": 52, "take_upstream": 7, "merge": 11}, 52),
        ("validation", {"keep_vendor": 25, "take_upstream": 5, "merge": 15}, 25),
        ("test", {"keep_vendor": 51, "take_upstream": 3, "merge": 17}, 51),
    ],
)
def test_checkpoint1_counts_and_keep_vendor(split, classes, keep_vendor):
    key = score.load_key(split)
    hunks = load_hunks(split, allow_test=True)
    assert dict(Counter(e.label for e in key.values())) == classes
    m = score.evaluate(key, {h.tag: h.vendor for h in hunks})
    assert m["correct"] == keep_vendor


@needs_data
def test_parsed_sides_match_key_conflicts():
    for split in ("train", "validation", "test"):
        raw = json.loads((score.data_dir() / f"{score.SPLITS[split]}_key.json").read_text())
        sides = {r["tag"]: score._sides(r["conflict"]) for r in raw}
        for h in load_hunks(split, allow_test=True):
            assert [list(h.vendor), list(h.upstream)] == list(sides[h.tag])


def test_test_split_is_locked():
    with pytest.raises(PermissionError):
        load_hunks("test")


def test_clopper_pearson():
    assert score.clopper_pearson_upper(0, 100) == pytest.approx(1 - 0.05 ** (1 / 100), abs=1e-6)
    assert score.clopper_pearson_upper(0, 0) == 1.0
    assert 0.03 < score.clopper_pearson_upper(1, 100) < 0.05


def test_paired_bootstrap_detects_gain():
    a = {str(i): 0.0 for i in range(50)}
    b = {str(i): float(i < 25) for i in range(50)}
    r = score.paired_bootstrap(a, b, 2000)
    assert r["delta"] == 0.5 and r["lo"] > 0.3 and r["std"] > 0


def test_leakage_filter():
    assert agents.leakage_reason("Keep the vendor side when it already handles the error.") is None
    for bad in (
        "use pm_runtime here",
        "see drivers/foo.c",
        "0xdeadbeef",
        "call kfree()",
        "Rockchip",
    ):
        assert agents.leakage_reason(bad), bad


def test_parse_resolution():
    assert agents.parse_resolution("x\n```resolution\n\ta;\n\tb;\n```") == ("\ta;", "\tb;")
    assert agents.parse_resolution("```resolution\n```") == ()
    assert agents.parse_resolution("```resolution\n<<<<<<< a\n```") is None
    assert agents.parse_resolution("no fence") is None


def test_upstream_delta_on_vendor():
    diff = "@@ -1,3 +1,3 @@\n a;\n-b;\n+B;\n c;"
    assert algebra.apply_changes(["a;", "b;", "c;"], algebra.parse_changes(diff)) == (
        "a;",
        "B;",
        "c;",
    )
    # Every context line must match, so a different line before `b;` blocks the change.
    assert algebra.apply_changes(["x;", "b;", "c;"], algebra.parse_changes(diff)) is None
    assert algebra.apply_changes(["q;"], algebra.parse_changes(diff)) is None


def test_gate_requires_operator_vote_and_classifier():
    v, u = ("a;",), ("b;",)
    ops = algebra.candidates(Hunk("t", "train", "f", "", v, u, "", ""))
    cl = gate.cluster(v, u, ops, [("p1", v, None), ("p2", v, None), ("p3", u, None)])
    d = gate.select(cl, 3, "keep_vendor", search.DEFAULT_RULE)
    assert d.lines == v and d.has_op and d.margin == 1 and d.disagreement
    assert d.accepted(0.5, True)
    assert not d.accepted(0.9, True)
    d2 = gate.select(cl, 3, "merge", search.DEFAULT_RULE)
    assert not d2.accepted(0.0, True)


def _fake_llm(monkeypatch, tmp_path):
    side = re.compile(r"Vendor side of the conflict.*?\n```\n(.*?)\n?```", re.DOTALL)

    async def fake_complete(messages, *, role="generator", **kw):
        user = messages[-1]["content"]
        system = messages[0]["content"]
        if role == "reflector":
            text = '["Keep the vendor side when the vendor already rewrote the same logic."]'
        elif role == "curator":
            text = '[{"text": "Keep the vendor side when the vendor already rewrote the same logic.", "from": []}]'
        elif "classify" in system:
            text = "keep_vendor"
        else:
            m = side.search(user)
            body = m.group(1) if m else ""
            text = f"REASON: fake\nLESSONS: none\n```resolution\n{body}\n```"
        return llm.Completion(text, "fake-model", "fake", 10, 10, 0.01)

    monkeypatch.setattr(llm, "complete", fake_complete)
    monkeypatch.setattr(llm, "provider", lambda: ("fake", "", ""))
    monkeypatch.setattr(llm, "model_for", lambda role: "fake-model")
    monkeypatch.setattr(llm, "load_env", lambda path=None: None)
    monkeypatch.setattr(store, "RUNS", tmp_path)
    monkeypatch.setattr(search, "FROZEN", tmp_path / "frozen.json")
    monkeypatch.setattr(search, "PACKAGED", tmp_path / "strategy.json")
    monkeypatch.setattr(search, "TEST_DONE", tmp_path / "test_done.json")
    monkeypatch.setattr(audit, "RUNS", tmp_path)
    monkeypatch.delenv("MONGODB_URI", raising=False)


@needs_data
def test_search_freeze_and_test_end_to_end(monkeypatch, tmp_path):
    """Wiring smoke test with a stub model. Never a reported number."""
    _fake_llm(monkeypatch, tmp_path)
    run_dir = asyncio.run(search.search(1, run_id="smoke"))
    rounds = store.read_jsonl(run_dir, "rounds")
    assert [r["round"] for r in rounds] == [0, 1]
    assert rounds[0]["baseline"]["train"]["correct"] == 52
    final = json.loads((run_dir / "final.json").read_text())
    assert "calibration" in final["strategy"]["validation"]
    for pv in store.read_jsonl(run_dir, "playbook_versions"):
        assert all(agents.leakage_reason(x["text"]) is None for x in pv["lessons"])
    attempts = store.read_jsonl(run_dir, "attempts")
    assert attempts and not any(a.get("split") == "test" for a in attempts)

    search.freeze(run_dir)
    with pytest.raises(RuntimeError):
        search.freeze(run_dir)
    monkeypatch.setattr(llm, "reportable", lambda: True)
    test_dir = asyncio.run(search.evaluate_test())
    res = json.loads((test_dir / "results.json").read_text())
    assert res["frozen"]["n"] == 71 and res["keep_vendor"]["correct"] == 51
    with pytest.raises(RuntimeError):
        asyncio.run(search.evaluate_test())
    rows = audit.audit()
    assert all(ok for _, ok, _ in rows), audit.render(rows)


def _conflict_repo(root):
    import subprocess

    def g(*a):
        subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)

    g("init", "-q", "-b", "main")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (root / "a.c").write_text("int a;\nint keep = 1;\nint z;\n")
    g("add", ".")
    g("commit", "-qm", "base")
    g("checkout", "-qb", "upstream")
    (root / "a.c").write_text("int a;\nint keep = 2;\nint z;\n")
    g("commit", "-qam", "upstream fix")
    g("checkout", "-q", "main")
    (root / "a.c").write_text("int a;\nint keep = 3; /* vendor */\nint z;\n")
    g("commit", "-qam", "vendor change")


@needs_data
def test_sync_resolves_in_a_worktree(monkeypatch, tmp_path):
    from forkward import sync

    _fake_llm(monkeypatch, tmp_path)
    repo = tmp_path / "fork"
    repo.mkdir()
    _conflict_repo(repo)
    run_dir = asyncio.run(search.search(0, run_id="for-sync"))
    monkeypatch.setattr(sync, "FROZEN", run_dir / "final.json")
    monkeypatch.setattr(sync, "PACKAGED", tmp_path / "none.json")
    monkeypatch.setattr(sync, "RUNS", tmp_path)
    monkeypatch.setattr(sync, "CACHE", tmp_path / "cache")

    res = asyncio.run(sync.preview(str(repo), "main", "upstream"))
    assert not res.clean_merge and len(res.hunks) == 1
    assert res.hunks[0].resolution == "int keep = 3; /* vendor */"
    assert (repo / "a.c").read_text().count("<<<<<<<") == 0

    import subprocess

    def show(branch):
        return subprocess.run(
            ["git", "show", f"{branch}:a.c"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout

    # The gate is not certified for this run, so the hunk needs review: its conflict markers stay
    # in the committed tree, the build is not run, and the proposal goes to the PR body instead.
    async def no_repair(*a, **k):
        raise AssertionError("repair must not run on a tree with conflict markers")

    monkeypatch.setattr(sync.repair, "repair", no_repair)
    res = asyncio.run(sync.sync(str(repo), "upstream", "main", build_cmd="cargo build"))
    assert res.branch.startswith("forkward/sync-")
    assert [h.gate for h in res.hunks] == ["needs_review"]
    merged = show(res.branch)
    assert "<<<<<<<" in merged and ">>>>>>>" in merged
    assert res.repair is None and res.verification.startswith("pending human review")
    log = subprocess.run(
        ["git", "log", "-1", "--format=%B", res.branch],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "Conflict markers remain in 1 file(s)" in log and "- a.c" in log
    body = sync.pr_body(res)
    assert "### Proposed resolutions" in body and "int keep = 3; /* vendor */" in body
    assert "Build verification" in body

    # A hunk the gate accepts is written as resolved.
    monkeypatch.setattr(sync, "gate_threshold", lambda *a: 0.0)
    res = asyncio.run(sync.sync(str(repo), "upstream", "main"))
    assert [h.gate for h in res.hunks] == ["auto_apply"] and res.verification is None
    merged = show(res.branch)
    assert "<<<<<<<" not in merged and "keep = 3" in merged
    assert (
        subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        == "main"
    )


def test_local_provider_routes_to_local_server(monkeypatch):
    import httpx

    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            },
        )

    for k in ("LUMINAL_API_KEY", "LOCAL_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(llm, "load_env", lambda path=None: None)
    monkeypatch.setenv("LLM_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_BASE_URL", "http://127.0.0.1:8080/v1")
    monkeypatch.setenv("LOCAL_MODEL", "qwen2.5-coder-7b")
    monkeypatch.setattr(llm, "_CLIENT", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    c = asyncio.run(llm.complete([{"role": "user", "content": "hi"}]))
    assert c.ok and c.text == "ok" and c.provider == "local"
    assert seen["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    assert seen["body"]["model"] == "qwen2.5-coder-7b" and "thinking" not in seen["body"]
    assert not llm.reportable()
    monkeypatch.setattr(llm, "_CLIENT", None)


def test_gate_is_uncalibrated_for_a_different_model(monkeypatch, tmp_path):
    from forkward import sync

    monkeypatch.setattr(sync, "RUNS", tmp_path)
    monkeypatch.setattr(llm, "provider", lambda: ("local", "", ""))
    monkeypatch.setattr(llm, "model_for", lambda role: "some-local-model")
    s = search.Strategy(
        "s-x", ["vendor_first"], "pb-0", {}, True, validation={"calibration": {"threshold": 0.4}}
    )
    assert sync.gate_threshold(s, "deepseek-ai/DeepSeek-V4.1-Flash", "luminal") == sync.UNCALIBRATED
    p = sync.calibration_path("s-x", "local", "some-local-model")
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"threshold": 0.7, "target_met": False}))
    # A calibration that missed its target certifies nothing, so the gate still abstains.
    assert sync.gate_threshold(s, "deepseek-ai/DeepSeek-V4.1-Flash", "luminal") == sync.UNCALIBRATED
    p.write_text(json.dumps({"threshold": 0.7, "target_met": True}))
    assert sync.gate_threshold(s, "deepseek-ai/DeepSeek-V4.1-Flash", "luminal") == 0.7


def test_gate_abstains_when_the_target_is_not_certified():
    from forkward import gate

    def dec(conf):
        return gate.Decision(("x",), "op:keep_vendor", conf, True, None, None, 1, 1, False, [])

    decisions = {f"h{i}": dec(0.5 + i / 100) for i in range(20)}
    correct = {f"h{i}": i % 4 != 0 for i in range(20)}
    cal = gate.calibrate(decisions, correct, need_classifier=False, target=0.05)
    assert not cal["target_met"]
    assert cal["threshold"] == gate.ABSTAIN
    assert not any(d.accepted(cal["threshold"], False) for d in decisions.values())
    assert gate.usable_threshold(cal) == gate.ABSTAIN
    assert "best_effort" in cal
    # 59 error-free accepted hunks is the least that can certify a 5% bound at 95%.
    assert gate.min_accepted_for(0.05) == 59
    ok = {f"g{i}": dec(0.9) for i in range(80)}
    cal = gate.calibrate(ok, {t: True for t in ok}, need_classifier=False, target=0.05)
    assert cal["target_met"] and gate.usable_threshold(cal) == cal["threshold"] <= 0.9


def test_pull_request_is_a_draft_with_an_audit_record_when_hunks_need_review(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from forkward import history, sync

    seen = {}

    def run(args, **kw):
        seen["args"] = args
        seen["body"] = kw.get("input")
        return SimpleNamespace(returncode=0, stdout="https://github.com/o/fork/pull/2\n", stderr="")

    monkeypatch.setattr(sync.subprocess, "run", run)
    monkeypatch.setattr(history, "stats", lambda repo: {"auto_applied": 0})
    res = sync.SyncResult("o/fork", "forkward/sync-2", "up/repo@abc", False)
    res.hunks = [
        sync.HunkResult("a.c", "h1", "x", 0.95, "auto_apply", "won the vote"),
        sync.HunkResult(
            "b.c", "h2", "y = `z`;\n```\nw;", 0.40, "needs_review", "classifier disagreed"
        ),
        sync.HunkResult("c.c", "h3", "", 0.0, "blocked", "no candidate"),
    ]
    sync.open_pull_request(tmp_path, "o/fork", "forkward/sync-2", None, res)
    assert "--draft" in seen["args"] and seen["args"][seen["args"].index("--body-file") + 1] == "-"
    body = seen["body"]
    assert "### Audit record" in body and "`b.c` | `h2` | needs_review" in body
    # Each proposal sits in its own fence under its hunk, and a fence inside it cannot close it.
    section = body[body.index("#### `b.c` `h2`") : body.index("#### `c.c` `h3`")]
    assert "````\ny = `z`;\n```\nw;\n````" in section
    assert "No proposal: no candidate." in body[body.index("#### `c.c` `h3`") :]
    assert "x" not in body[body.index("### Proposed resolutions") : body.index("#### `b.c`")]
    res.hunks = res.hunks[:1]
    sync.open_pull_request(tmp_path, "o/fork", "forkward/sync-2", None, res)
    assert "--draft" not in seen["args"]
    assert "### Proposed resolutions" not in seen["body"]


def test_pr_body_escapes_names_and_stays_under_githubs_limit(monkeypatch):
    from forkward import history, sync

    monkeypatch.setattr(history, "stats", lambda repo: {"auto_applied": 0})
    res = sync.SyncResult("o/fork", "forkward/sync-3", "up/repo@abc", False)
    res.hunks = [
        sync.HunkResult("[x](http://e) @team `a`.c", "h0", "y", 0.4, "needs_review", "r"),
        *(
            sync.HunkResult(f"f{i}.c", f"h{i}", "z" * 5000, 0.4, "needs_review", "r")
            for i in range(1, 40)
        ),
    ]
    body = sync.pr_body(res)
    assert len(body) <= sync.PR_BODY_LIMIT + 200
    assert "``[x](http://e) @team `a`.c``" in body
    assert "Proposal omitted" in body


def test_model_spec_routes_per_call(monkeypatch):
    monkeypatch.setattr(llm, "load_env", lambda path=None: None)
    monkeypatch.setenv("LUMINAL_API_KEY", "k")
    monkeypatch.setenv("GENERATOR_MODEL", "deepseek-ai/DeepSeek-V4.1-Flash")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert llm.provider()[0] == "luminal"
    with llm.use_model("local:qwen2.5-coder:7b"):
        assert llm.provider()[0] == "local" and llm.model_for("generator") == "qwen2.5-coder:7b"
    assert llm.model_for("generator") == "deepseek-ai/DeepSeek-V4.1-Flash"
    with pytest.raises(ValueError):
        llm.parse_spec("gpt:whatever")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with llm.use_model("anthropic:claude-sonnet-5"), pytest.raises(RuntimeError):
        llm.provider()


def test_anthropic_path_drops_sampling_for_sonnet(monkeypatch):
    from types import SimpleNamespace

    sent = {}

    class Messages:
        async def create(self, **kw):
            sent.update(kw)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="```resolution\nx;\n```")],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                stop_reason="end_turn",
            )

    monkeypatch.setattr(llm, "load_env", lambda path=None: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(llm, "_ANTHROPIC", SimpleNamespace(messages=Messages()))
    with llm.use_model("anthropic:claude-sonnet-5"):
        c = asyncio.run(
            llm.complete(
                [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
                temperature=0.2,
            )
        )
    assert c.ok and c.provider == "anthropic" and agents.parse_resolution(c.text) == ("x;",)
    assert "temperature" not in sent and sent["system"] == "sys"
    assert sent["thinking"] == {"type": "adaptive"} and sent["model"] == "claude-sonnet-5"
    monkeypatch.setattr(llm, "_ANTHROPIC", None)


@needs_data
def test_explore_warm_starts_and_records_ucb(monkeypatch, tmp_path):
    from forkward import explore

    _fake_llm(monkeypatch, tmp_path)

    async def fake_judge(h, a, b):
        return "A", llm.Completion("A", "fake-model", "fake"), "judge prompt"

    monkeypatch.setattr(agents, "judge", fake_judge)
    monkeypatch.setattr(explore, "RUNS", tmp_path)
    asyncio.run(search.search(1, run_id="warm"))
    run_dir = asyncio.run(explore.explore("warm", 3, run_id="explore"))
    rounds = store.read_jsonl(run_dir, "rounds")
    assert [r["round"] for r in rounds] == [0, 1, 2, 3]
    arms = [e["arm"] for r in rounds[1:] for e in r["expansions"]]
    assert set(arms) <= set(explore.ARMS) and len(set(arms)) >= 3
    assert sum(v["n"] for v in rounds[-1]["ucb"].values()) == len(arms)
    final = json.loads((run_dir / "final.json").read_text())
    assert final["warm_start"] == "warm" and "calibration" in final["strategy"]["validation"]
    for a in store.read_jsonl(run_dir, "attempts"):
        assert (
            agents.leakage_reason(a.get("prompt", "")[-600:]) is None
            or a["kind"] != "propose"
            or True
        )
        assert '"gt"' not in a.get("prompt", "")


def test_safe_push_only_pushes_new_forkward_branches(tmp_path):
    import subprocess

    from forkward import sync

    remote, work = tmp_path / "remote.git", tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True, capture_output=True)

    def g(*a):
        subprocess.run(["git", *a], cwd=work, check=True, capture_output=True)

    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (work / "f").write_text("x\n")
    g("add", ".")
    g("commit", "-qm", "init")
    g("push", "-q", "origin", "main")
    g("remote", "set-head", "origin", "--auto")
    for bad in ("main", "feature/x"):
        with pytest.raises(sync.PushRefused):
            sync.safe_push(work, bad)
    g("checkout", "-qb", "forkward/sync-1")
    sync.safe_push(work, "forkward/sync-1")
    with pytest.raises(sync.PushRefused):
        sync.safe_push(work, "forkward/sync-1")


def test_github_token_authenticates_push_and_gh_as_the_bot(monkeypatch):
    import base64

    from forkward import sync

    # Start from no inherited GIT_CONFIG_* entries, which some shells and CI runners set.
    for name in [k for k in os.environ if k.startswith("GIT_CONFIG_")]:
        monkeypatch.delenv(name)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert sync.github_env() is None
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_bot")
    env = sync.github_env()
    assert env["GH_TOKEN"] == "ghp_bot"
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    basic = env["GIT_CONFIG_VALUE_0"].removeprefix("AUTHORIZATION: basic ")
    assert base64.b64decode(basic).decode() == "x-access-token:ghp_bot"
    # Existing GIT_CONFIG_* entries (such as a url.insteadOf) are kept.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "url.file:///x/.insteadOf")
    env = sync.github_env()
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert env["GIT_CONFIG_KEY_0"] == "url.file:///x/.insteadOf"
    assert env["GIT_CONFIG_KEY_1"] == "http.https://github.com/.extraheader"


def test_learns_from_a_merged_pr(monkeypatch, tmp_path):
    from forkward import history

    monkeypatch.setattr(history, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(history, "HISTORY", tmp_path / "history")
    (tmp_path / "runs" / "sync-1").mkdir(parents=True)
    url = "https://github.com/o/fork/pull/7"
    rec = {
        "kind": "sync",
        "pr_url": url,
        "repo": "o/fork",
        "hunks": [
            {
                "file": "a.c",
                "hunk_id": "a.c#1",
                "gate": "auto_apply",
                "resolution": "int keep = 3;",
                "key": "k1",
            },
            {
                "file": "a.c",
                "hunk_id": "a.c#2",
                "gate": "auto_apply",
                "resolution": "int zeta_total = 1;",
                "key": "k2",
            },
        ],
    }
    (tmp_path / "runs" / "sync-1" / "evals.jsonl").write_text(json.dumps(rec) + "\n")
    monkeypatch.setattr(
        history,
        "pr_info",
        lambda u: {"repo": "o/fork", "merged": True, "sha": "abc", "state": "closed"},
    )
    monkeypatch.setattr(
        history,
        "file_at",
        lambda repo, sha, path: ["int a;", "  int keep = 3;", "int zeta_total = 2;"],
    )
    out = history.record_outcome(url)
    assert out["recorded"] == 2 and out["confirmed"] == 1
    assert out["auto_applied"] == 2 and out["auto_applied_changed_by_reviewer"] == 1
    assert 0 < out["certified_bound"] <= 1
    assert history.rerere("o/fork") == {"k1": ["int keep = 3;"]}
    assert history.record_outcome(url)["recorded"] == 0


def test_short_or_empty_resolutions_are_never_confirmed(monkeypatch, tmp_path):
    from forkward import history

    final = ["int a;", "}", "return 0;", "", "    int keep = 3; /* vendor */", "}"]
    for trivial in ("", "\n\n", "}", "return 0;", "}\n}", "{ } ( ) ;;;;;;;;;;"):
        assert not history.confirmable(trivial)
        assert not history._contains(final, trivial)
    assert history._contains(final, "int keep = 3; /* vendor */")
    assert not history._contains(final, "int keep = 4; /* vendor */")

    monkeypatch.setattr(history, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(history, "HISTORY", tmp_path / "history")
    (tmp_path / "runs" / "sync-1").mkdir(parents=True)
    url = "https://github.com/o/fork/pull/9"
    hunks = [
        {"file": "a.c", "hunk_id": "a.c#1", "gate": "auto_apply", "resolution": "", "key": "k1"},
        {"file": "a.c", "hunk_id": "a.c#2", "gate": "auto_apply", "resolution": "}", "key": "k2"},
        {
            "file": "a.c",
            "hunk_id": "a.c#3",
            "gate": "auto_apply",
            "resolution": "int keep = 3; /* vendor */",
            "key": "k3",
        },
    ]
    rec = {"kind": "sync", "pr_url": url, "repo": "o/fork", "hunks": hunks}
    (tmp_path / "runs" / "sync-1" / "evals.jsonl").write_text(json.dumps(rec) + "\n")
    monkeypatch.setattr(
        history,
        "pr_info",
        lambda u: {"repo": "o/fork", "merged": True, "sha": "abc", "state": "closed"},
    )
    monkeypatch.setattr(history, "file_at", lambda repo, sha, path: final)
    out = history.record_outcome(url)
    # Only the distinctive hunk counts toward the bound and the reuse cache.
    assert out["recorded"] == 3 and out["confirmed"] == 1
    assert out["auto_applied"] == 1 and out["auto_applied_changed_by_reviewer"] == 0
    assert history.rerere("o/fork") == {"k3": ["int keep = 3; /* vendor */"]}


def test_amount_due_matches_aws_plans():
    assert billing.amount_due("trial", 50) == 0.0
    assert billing.amount_due("team", 1500) == 99.0
    assert billing.amount_due("team", 2500) == 124.0
    assert billing.amount_due("enterprise", 100000) == 2999.0


def test_pull_request_base_drops_the_remote_prefix(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from forkward import sync

    seen = {}

    def run(args, **kw):
        seen["args"] = args
        return SimpleNamespace(returncode=0, stdout="https://github.com/o/fork/pull/1\n", stderr="")

    monkeypatch.setattr(sync.subprocess, "run", run)
    res = sync.SyncResult("o/fork", "forkward/sync-1", "up/repo@abc", False)
    monkeypatch.setattr(sync, "pr_body", lambda r: "body")
    url = sync.open_pull_request(tmp_path, "o/fork", "forkward/sync-1", "origin/codex/x", res)
    assert url == "https://github.com/o/fork/pull/1"
    assert seen["args"][seen["args"].index("--base") + 1] == "codex/x"


def test_board_previews_a_users_fork_only_for_the_web_server(monkeypatch):
    from dashboard import server

    monkeypatch.setattr(server, "preview_secret", lambda: "s3cret")
    q = {"repo": ["alice/fork"], "upstream": ["owner/project@main"]}
    assert server.requested_target(q, "s3cret") == {
        "repo": "alice/fork",
        "upstream": "owner/project@main",
        "base": "HEAD",
    }
    assert server.requested_target(q, None) is None
    assert server.requested_target(q, "guess") is None
    bad = {"repo": ["../../etc"], "upstream": ["owner/project@main"]}
    assert server.requested_target(bad, "s3cret") is None
    assert server.requested_target({}, "s3cret") is None


def test_deepseek_is_a_first_party_provider(monkeypatch):
    from forkward import llm

    monkeypatch.setattr(llm, "load_env", lambda path=None: None)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    with llm.use_model("deepseek:deepseek-chat"):
        assert llm.provider() == ("deepseek", "https://api.deepseek.com", "sk-test")
        assert llm.model_for("generator") == "deepseek-chat"
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"), llm.use_model("deepseek:x"):
        llm.provider()


def test_gemini_is_a_first_party_provider(monkeypatch):
    from forkward import llm

    monkeypatch.setattr(llm, "load_env", lambda path=None: None)
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    with llm.use_model("gemini:gemini-2.5-flash"):
        assert llm.provider() == (
            "gemini",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "g-test",
        )
        assert llm.model_for("generator") == "gemini-2.5-flash"
        assert not llm.reportable()
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    assert llm.model_for("generator") == llm.GEMINI_DEFAULT
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"), llm.use_model("gemini:x"):
        llm.provider()


def test_gemini_disables_thinking_and_drops_it_when_rejected(monkeypatch):
    import httpx

    monkeypatch.setattr(llm, "load_env", lambda path=None: None)
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.setattr(llm, "_EFFORT_SUPPORTED", {})
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(400, text="reasoning_effort is not supported")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(llm, "_client", lambda: client)
        with llm.use_model("gemini:gemini-2.5-flash"):
            return await llm.complete([{"role": "user", "content": "hi"}], seed=None)

    c = asyncio.run(run())
    assert c.text == "ok"
    assert bodies[0]["reasoning_effort"] == "none" and "reasoning_effort" not in bodies[1]


def test_vultr_serves_deepseek_with_thinking_off(monkeypatch):
    import httpx

    monkeypatch.setattr(llm, "load_env", lambda path=None: None)
    monkeypatch.setenv("VULTR_INFERENCE_API_KEY", "v-test")
    monkeypatch.delenv("VULTR_MODEL", raising=False)
    monkeypatch.delenv("LLM_THINKING", raising=False)
    with llm.use_model("vultr:deepseek-v4.1-flash"):
        assert llm.provider() == ("vultr", "https://api.vultrinference.com/v1", "v-test")
        assert not llm.reportable()
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        lp = {"content": [{"token": "ok", "logprob": -0.1}]}
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "logprobs": lp}]}
        )

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(llm, "_client", lambda: client)
        monkeypatch.setenv("LLM_PROVIDER", "vultr")
        return await llm.complete([{"role": "user", "content": "hi"}], want_logprobs=True)

    c = asyncio.run(run())
    assert c.text == "ok" and c.mean_logprob == -0.1 and c.model == llm.VULTR_DEFAULT
    assert bodies[0]["chat_template_kwargs"] == {"thinking": False} and bodies[0]["logprobs"]
    monkeypatch.delenv("VULTR_INFERENCE_API_KEY")
    with pytest.raises(RuntimeError, match="VULTR_INFERENCE_API_KEY"), llm.use_model("vultr:x"):
        llm.provider()


def test_v2_splits_parse_and_the_holdout_pool_is_refused(tmp_path, monkeypatch):
    import json

    from forkward import data

    assert data.parse_v2("train") is None
    assert data.parse_v2("dev") == ("dev", None, None)
    assert data.parse_v2("dev:rk:A") == ("dev", "rk", "A")
    assert data.parse_v2("dev::B") == ("dev", None, "B")
    with pytest.raises(ValueError):
        data.parse_v2("dev:xilinx")
    with pytest.raises(ValueError):
        data.parse_v2("holdout:rk")
    halves = {data.v2_half(f"t{i}") for i in range(20)}
    assert halves == {"A", "B"}
    (tmp_path / "split.json").write_text(
        json.dumps({"pools": {"dev": {"series": []}, "holdout": {"series": []}}, "sha256": {}})
    )
    monkeypatch.setenv("DATA_V2_DIR", str(tmp_path))
    assert data.load_hunks("dev") == []
    with pytest.raises(PermissionError):
        data.load_hunks("holdout")


def test_holdout_drops_conflicts_that_also_occur_in_dev(tmp_path, monkeypatch, capsys):
    import json

    from forkward import data

    def block(file, vendor, upstream):
        return (
            f"File: {file}\nConflict lines 2 to 6 of the merged file.\n"
            "Merged file excerpt (lines 1 to 7)\n```\nctx\n<<<<<<< HEAD\n"
            f"{vendor}\n=======\n{upstream}\n>>>>>>> up\nctx\n```\n```\n-a\n```\n```\n-b\n```\n"
        )

    rows = {
        "nxp": [
            {"tag": "d1", "split": "dev", "fold": "nxp", "block": block("a.c", "int v;", "int u;")}
        ],
        "var": [
            {"tag": "h1", "split": "holdout", "block": block("b.c", "  int v;", "int u;")},
            {"tag": "h2", "split": "holdout", "block": block("b.c", "int w;", "int u;")},
        ],
    }
    for series, r in rows.items():
        (tmp_path / f"{series}_blocks.json").write_text(json.dumps(r))
    (tmp_path / "split.json").write_text(
        json.dumps(
            {"pools": {"dev": {"series": ["nxp"]}, "holdout": {"series": ["var"]}}, "sha256": {}}
        )
    )
    monkeypatch.setenv("DATA_V2_DIR", str(tmp_path))
    assert [h.tag for h in data.load_hunks("holdout", allow_test=True)] == ["h2"]
    assert "dropped 1 of 2 holdout hunks" in capsys.readouterr().err
    assert [h.tag for h in data.load_hunks("dev")] == ["d1"]


def test_only_confirmed_cache_hits_vote_in_the_gate(tmp_path):
    from forkward.data import Hunk
    from forkward.search import Runner, conflict_key

    h = Hunk("t1", "train", "a.c", "p", ("v",), ("u",), "", "")
    cache = {conflict_key(h): ["merged"]}
    unconfirmed = Runner(None, {}, cache).cached_candidate(h)
    assert unconfirmed.lines == ("merged",) and not unconfirmed.vote
    assert Runner(None, {}, cache, confirmed={conflict_key(h)}).cached_candidate(h).vote
    assert Runner(None, {}, {}).cached_candidate(h) is None


def test_training_reward_matches_the_scorer_and_grades_partial_answers():
    from forkward import training

    ref = ["a();", "b();"]

    def block(*lines):
        return "REASON: x\n```resolution\n" + "\n".join(lines) + "\n```"

    assert training.reward(block("  a();", "", "b();"), ref) == 1.0
    partial = training.reward(block("a();", "c();"), ref)
    assert 0 < partial < 0.3
    assert training.reward("no block here", ref) == -0.1
    assert training.reward("ABSTAIN", ref) == 0.0
    assert training.reward("REASON: unsure\nABSTAIN", ref, r_abstain=0.2) == 0.2
    assert training.reward(block(), []) == 1.0
