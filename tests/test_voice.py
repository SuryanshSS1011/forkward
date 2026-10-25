"""ElevenLabs spoken digests: optional, never raising, and written for the ear."""

import asyncio
import json
from types import SimpleNamespace

import httpx

from forkward import schedule, voice


def test_digest_text_reads_naturally():
    assert voice.digest_text("acme/linux-imx", 0, 0, 0, None) == (
        "Scheduled sync for linux imx: upstream merged cleanly, with no conflicts."
    )
    assert voice.digest_text("acme/linux", 3, 2, 1, "https://github.com/acme/linux/pull/7") == (
        "Scheduled sync for linux: 3 conflicts. 2 resolved automatically, and 1 needs a person."
        " A pull request is open."
    )
    assert voice.digest_text("acme/linux", 1, 1, 0, None).endswith(
        "1 conflict. 1 resolved automatically."
    )


def test_speak_writes_mp3_and_survives_failures(monkeypatch, tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        if json.loads(request.content)["text"] == "fail":
            return httpx.Response(401, text="invalid api key")
        return httpx.Response(200, content=b"ID3audio")

    async def run(text):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await voice.speak(text, tmp_path / "d.mp3", client=c)

    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert asyncio.run(run("hello")) is False and not seen
    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi-test")
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    assert asyncio.run(run("hello")) is True
    assert (tmp_path / "d.mp3").read_bytes() == b"ID3audio"
    assert seen[0].url.path == f"/v1/text-to-speech/{voice.DEFAULT_VOICE}"
    assert seen[0].headers["xi-api-key"] == "xi-test"
    (tmp_path / "d.mp3").unlink()
    assert asyncio.run(run("fail")) is False and not (tmp_path / "d.mp3").exists()


def test_scheduled_sync_alert_links_its_spoken_digest(monkeypatch, tmp_path):
    from forkward import history, sandbox, store

    monkeypatch.setattr(store, "RUNS", tmp_path)
    monkeypatch.setattr(
        schedule, "entries", lambda: [{"repo": "acme/linux", "upstream": "torvalds/linux@v6.6"}]
    )
    monkeypatch.setattr(history, "learn_pending", list)
    monkeypatch.setattr(sandbox, "hosted", lambda: False)
    res = SimpleNamespace(
        needs_review=1, clean_merge=False, hunks=[1, 2], auto_applied=1, pr_url=None
    )

    async def preview(*a, **k):
        return res

    async def speak(text, dest, client=None):
        dest.write_bytes(b"ID3")
        return True

    monkeypatch.setattr(schedule.sync, "preview", preview)
    monkeypatch.setattr(voice, "speak", speak)
    out = asyncio.run(schedule.run_all())
    assert out[0]["ok"]
    alerts = [
        json.loads(line)
        for f in tmp_path.glob("*/events.jsonl")
        for line in f.read_text().splitlines()
    ]
    alert = next(a for a in alerts if a.get("audio"))
    assert (tmp_path / alert["audio"] / "digest.mp3").read_bytes() == b"ID3"
    assert alert["spoken"].startswith("Scheduled sync for linux: 2 conflicts.")
