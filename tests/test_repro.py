import asyncio
import json

import httpx
import pytest

from forkward import agents, llm, search, store
from tests.test_core import _fake_llm, needs_data


def test_ids_are_content_hashes():
    lessons = [agents.Lesson(agents.lesson_id("Keep the vendor side."), "Keep the vendor side.")]
    assert agents.playbook_id("pb-0", lessons) == agents.playbook_id("pb-0", list(lessons))
    assert agents.playbook_id("pb-0", lessons) != agents.playbook_id("pb-1", lessons)
    lid = lessons[0].id
    assert lid.startswith("L") and lid[1:].isdigit()
    pb = agents.Playbook("pb-x", None, lessons)
    assert agents._lessons_used(f"LESSONS: {lid}", pb) == (lid,)
    assert search.new_id("s", ["a"], "pb-0") == search.new_id("s", ["a"], "pb-0")
    assert search.stable_seed(0, "t", 1) != search.stable_seed(1, "t", 1)


def test_seed_is_sent_and_dropped_when_rejected(monkeypatch):
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "seed" in body:
            return httpx.Response(400, text="unknown field: seed")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    monkeypatch.setattr(llm, "load_env", lambda path=None: None)
    monkeypatch.setenv("LLM_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_BASE_URL", "http://127.0.0.1:8080/v1")
    monkeypatch.setenv("LOCAL_MODEL", "seed-test-model")
    monkeypatch.setattr(llm, "_CLIENT", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(llm, "_SEED_SUPPORTED", {})
    c = asyncio.run(llm.complete([{"role": "user", "content": "hi"}], seed=7))
    assert c.ok and bodies[0]["seed"] == 7 and "seed" not in bodies[1]
    c = asyncio.run(llm.complete([{"role": "user", "content": "hi"}], seed=7))
    assert c.ok and "seed" not in bodies[-1]
    monkeypatch.setattr(llm, "_CLIENT", None)


@needs_data
def test_two_runs_of_search_give_the_same_ids_and_seeds(monkeypatch, tmp_path):
    _fake_llm(monkeypatch, tmp_path)
    ids = []
    for run in ("a", "b"):
        d = asyncio.run(search.search(1, run_id=run))
        strategies = sorted(s["id"] for s in store.read_jsonl(d, "strategies"))
        playbooks = sorted({p["version"] for p in store.read_jsonl(d, "playbook_versions")})
        seeds = sorted(
            (a["tag"], a["persona"], a["seed"])
            for a in store.read_jsonl(d, "attempts")
            if a["kind"] == "propose"
        )
        ids.append((strategies, playbooks, seeds))
    assert ids[0] == ids[1]


@needs_data
def test_report_rebuilds_what_it_can_and_marks_the_rest(monkeypatch, tmp_path):
    from forkward import analysis

    _fake_llm(monkeypatch, tmp_path / "runs")
    monkeypatch.setattr(analysis, "RUNS", tmp_path / "runs")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "results").mkdir()
    frozen = '{"frozen": "do not touch"}'
    (tmp_path / "results" / "test-results.json").write_text(frozen)
    rows = {r["file"]: r for r in analysis.report()}
    assert rows["operators.json"]["status"] == "regenerated"
    assert rows["selector-h0.json"]["status"] == "regenerated"
    assert rows["ladder-search-2-validation.json"]["status"] == "skipped"
    assert rows["test-results.json"]["status"] == "from another command"
    assert (tmp_path / "results" / "test-results.json").read_text() == frozen
    assert (tmp_path / "results" / "PROVENANCE.json").exists()
    assert analysis.report_main(["--strict"]) == 1

    asyncio.run(search.search(1, run_id="search-2"))
    rows = {r["file"]: r for r in analysis.report()}
    for name in (
        "ladder-search-2-train.json",
        "learning-curve.json",
        "parallelism-search-2-validation.json",
    ):
        assert rows[name]["status"] == "regenerated", name
    curve = json.loads((tmp_path / "results" / "learning-curve.json").read_text())
    assert [r["round"] for r in curve["rounds"]] == [0, 1]


def test_bundle_runs_is_byte_identical_and_lists_every_file(tmp_path):
    import hashlib
    import tarfile

    from forkward import repro

    runs = tmp_path / "runs"
    (runs / "a").mkdir(parents=True)
    (runs / "a" / "events.jsonl").write_text('{"x": 1}\n')
    (runs / "b.json").write_text("{}")
    t1, m1 = repro.bundle(runs, tmp_path / "one.tar.gz")
    t2, _ = repro.bundle(runs, tmp_path / "two.tar.gz")
    assert t1.read_bytes() == t2.read_bytes()
    with tarfile.open(t1) as tar:
        names = tar.getnames()
        manifest = tar.extractfile("MANIFEST.sha256").read().decode()
    assert names == ["MANIFEST.sha256", "runs/a/events.jsonl", "runs/b.json"]
    want = hashlib.sha256(b'{"x": 1}\n').hexdigest()
    assert f"{want}  runs/a/events.jsonl" in manifest
    assert m1.read_text().startswith(manifest)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        repro.bundle(empty, tmp_path / "x.tar.gz")
