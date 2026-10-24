import asyncio

import pytest

from forkward import agents, audit, search, store, vocab
from tests.test_core import _fake_llm, needs_data


@needs_data
def test_audit_fails_when_there_are_no_run_logs(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "RUNS", tmp_path / "runs")
    rows = audit.audit()
    name, ok, evidence = rows[1]
    assert name.startswith("2.") and not ok and "nothing to audit" in evidence


@needs_data
def test_curator_prompts_are_logged_and_audited(monkeypatch, tmp_path):
    _fake_llm(monkeypatch, tmp_path)
    run = asyncio.run(search.search(1, run_id="curate"))
    curate = [a for a in store.read_jsonl(run, "attempts") if a["kind"] == "curate"]
    assert curate and all(a["prompt"].startswith("Current playbook:") for a in curate)
    rows = audit.audit()
    assert rows[1][1] and "curate" in rows[1][2]


@needs_data
@pytest.mark.parametrize(
    "lesson", ["call kfree on error", "use devm helpers", "the hdmi block", "edma channel",
               "dwc3 glue", "i2c bus", "pinctrl state", "GFP flags"],
)  # fmt: skip
def test_lesson_filter_rejects_lowercase_identifiers_from_any_split(lesson):
    assert agents.leakage_reason(lesson)


@needs_data
def test_lesson_filter_keeps_plain_english_lessons():
    for lesson in (
        "Keep the vendor side when it already handles the error.",
        "When the vendor rewrote the same logic, keep the vendor version.",
        "Take the upstream fix when the vendor code still has the bug it corrects.",
        "Merge both sides when each adds a separate entry to the same table.",
    ):
        assert agents.leakage_reason(lesson) is None, lesson


def test_english_check_handles_inflections_and_code_abbreviations(monkeypatch):
    words = frozenset({"handle", "change", "copy", "dev"})
    assert vocab.is_english("handles", words) and vocab.is_english("changed", words)
    assert vocab.is_english("copies", words) and vocab.is_english("using", words)
    assert not vocab.is_english("dev", words)
    monkeypatch.setattr(vocab, "identifier_vocabulary", lambda root=None: frozenset({"kfree"}))
    assert vocab.data_identifier("never call kfree twice") == "kfree"
    monkeypatch.setattr(vocab, "identifier_vocabulary", lambda root=None: frozenset())
    assert vocab.data_identifier("never call kfree twice") is None


def test_code_tokens_skip_comments_and_split_identifiers():
    block = '```\nstatic int foo_barBaz(void) /* kfree here */\n{\n\treturn "devm";\n}\n```'
    toks = vocab.code_tokens(block)
    assert {"foo", "bar", "baz", "foo_barbaz", "static", "return"} <= toks
    assert "kfree" not in toks and "devm" not in toks
