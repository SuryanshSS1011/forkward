"""Spoken digests of scheduled syncs through ElevenLabs text-to-speech. Optional: without
ELEVENLABS_API_KEY nothing is spoken, and a failed call is logged, never raised."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

API = "https://api.elevenlabs.io/v1/text-to-speech"
# "George", one of ElevenLabs' default voices, available on every account.
DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"
DEFAULT_MODEL = "eleven_flash_v2_5"
DIGEST = "digest.mp3"


def enabled() -> bool:
    return bool(os.environ.get("ELEVENLABS_API_KEY"))


def digest_text(repo: str, hunks: int, auto: int, review: int, pr_url: str | None) -> str:
    """The summary written for the ear: no arrows, refs or URLs."""
    name = repo.split("/")[-1].replace("-", " ").replace("_", " ")
    if not hunks:
        said = f"Scheduled sync for {name}: upstream merged cleanly, with no conflicts."
    else:
        said = (
            f"Scheduled sync for {name}: {hunks} {'conflict' if hunks == 1 else 'conflicts'}. "
            f"{auto} resolved automatically"
        )
        said += f", and {review} {'needs' if review == 1 else 'need'} a person." if review else "."
    if pr_url:
        said += " A pull request is open."
    return said


async def speak(text: str, dest: Path, client: httpx.AsyncClient | None = None) -> bool:
    """Write `text` as MP3 to `dest`. Returns whether a file was written."""
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        return False
    voice = os.environ.get("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE
    body = {"text": text, "model_id": os.environ.get("ELEVENLABS_MODEL") or DEFAULT_MODEL}
    own = client is None
    client = client or httpx.AsyncClient(timeout=30.0)
    try:
        r = await client.post(
            f"{API}/{voice}",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": key, "Accept": "audio/mpeg"},
            json=body,
        )
    except httpx.HTTPError as e:
        print(f"[voice] ElevenLabs request failed: {type(e).__name__}: {e}", file=sys.stderr)
        return False
    finally:
        if own:
            await client.aclose()
    if r.status_code != 200 or not r.content:
        print(f"[voice] ElevenLabs HTTP {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return False
    dest.write_bytes(r.content)
    return True
