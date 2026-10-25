"""Scheduled syncs: run every configured fork against its upstream and post the result as an alert.

Config: FORKWARD_SCHEDULES (default ~/.forkward/schedules.json), a list of
  {"repo": "owner/name", "upstream": "owner/name@ref", "base": "branch", "build": "cmd", "open_pr": true}
With GitHub credentials and open_pr, a pull request is opened into the base branch; otherwise the
run is a preview. Hunks the gate is unsure about are always flagged for a person. With
ELEVENLABS_API_KEY set, each result is also spoken to runs/<run_id>/digest.mp3 for the board.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import traceback
from pathlib import Path

from forkward import home, sync, voice
from forkward.store import Store

CONFIG = Path(os.environ.get("FORKWARD_SCHEDULES") or home() / "schedules.json")


def entries() -> list[dict]:
    if not CONFIG.exists():
        return []
    return json.loads(CONFIG.read_text())


async def run_all() -> list[dict]:
    from forkward import history
    from forkward.mcp_server import _github_ready

    out = []
    for r in history.learn_pending():
        if r.get("recorded"):
            learned = Store(f"learned-{time.strftime('%Y%m%dT%H%M%S')}")
            learned.event(
                "learner",
                "alert",
                level="info",
                url=r["pr"],
                message=f"learned from merged {r['pr']}: {r['recorded']} hunks, "
                f"{r['confirmed']} confirmed so far for {r['repo']}",
            )
            learned.close()
    from forkward import sandbox

    hosted = sandbox.hosted()
    for e in entries():
        # Schedules are the operator's own configuration, so their runs stay on the public board.
        store = Store(sync.run_id("scheduled", private=False))
        try:
            if hosted and (e.get("open_pr") or e.get("build") or e.get("test")):
                # The hosted box never pushes and never runs untrusted builds; it previews only.
                store.event(
                    "scheduler",
                    "alert",
                    level="info",
                    message=f"scheduled sync {e.get('repo')}: hosted mode previews only, so "
                    "open_pr, build and test are ignored",
                )
            if e.get("open_pr") and not hosted and _github_ready():
                res = await sync.sync(
                    e["repo"],
                    e["upstream"],
                    e.get("base"),
                    e.get("build"),
                    e.get("test"),
                    open_pr=True,
                )
            else:
                res = await sync.preview(
                    e["repo"],
                    e.get("base") or "HEAD",
                    e["upstream"],
                    run=sync.run_id("preview", private=False),
                )
            review = res.needs_review
            msg = (
                f"scheduled sync {e['repo']} <- {e['upstream']}: "
                + ("clean merge" if res.clean_merge else f"{len(res.hunks)} conflict hunks")
                + f", {res.auto_applied} auto-applied, {review} need a person"
                + (f", PR {res.pr_url}" if res.pr_url else "")
            )
            said = voice.digest_text(
                e["repo"], len(res.hunks), res.auto_applied, review, res.pr_url
            )
            spoken = await voice.speak(said, store.dir / voice.DIGEST)
            store.event(
                "scheduler",
                "alert",
                level="needs_review" if review else "scheduled_sync",
                message=msg,
                url=res.pr_url,
                **({"audio": store.run_id, "spoken": said} if spoken else {}),
            )
            out.append({"repo": e["repo"], "ok": True, "message": msg})
        except Exception as err:  # noqa: BLE001 - one failing repo must not stop the others
            store.event(
                "scheduler",
                "alert",
                level="warn",
                message=f"scheduled sync {e.get('repo')} failed: {err}",
            )
            out.append(
                {
                    "repo": e.get("repo"),
                    "ok": False,
                    "error": str(err),
                    "trace": traceback.format_exc()[-800:],
                }
            )
        finally:
            store.close()
    return out


def main(argv: list[str]) -> int:
    results = asyncio.run(run_all())
    print(json.dumps(results, indent=1))
    return 0 if all(r["ok"] for r in results) else 1
