"""Learn from merged pull requests: every hunk a person merged unchanged is confirmed history.

Confirmed resolutions are reused when the same conflict comes back (like git rerere), and the
auto-applied hunks a reviewer did or did not have to change give each repository its own error
bound, which tightens with every merge. Reads use the repository's installation token when the
GitHub App covers it, else GITHUB_TOKEN, else go anonymous, which works for public repositories.
"""

from __future__ import annotations

import json
import os
import re
import time
from urllib.parse import quote

import httpx

from forkward import github_app, home
from forkward.score import clopper_pearson_upper, normalize
from forkward.store import RUNS, read_jsonl

HISTORY = home() / "history"


def _slug(repo: str) -> str:
    return repo.replace("/", "__")


def _headers(repo: str) -> dict:
    try:
        tok = github_app.token_for(repo)
    except (github_app.GitHubAppError, httpx.HTTPError):
        tok = os.environ.get("GITHUB_TOKEN")
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def pr_info(pr_url: str) -> dict:
    m = re.match(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)", pr_url)
    if not m:
        raise ValueError(f"not a GitHub pull request URL: {pr_url}")
    r = httpx.get(f"{github_app.API}/repos/{m[1]}/pulls/{m[2]}", headers=_headers(m[1]), timeout=30)
    r.raise_for_status()
    d = r.json()
    return {
        "repo": m[1],
        "merged": bool(d.get("merged")),
        "sha": d.get("merge_commit_sha"),
        "state": d.get("state"),
    }


def file_at(repo: str, sha: str, path: str) -> list[str] | None:
    r = httpx.get(
        f"{github_app.API}/repos/{repo}/contents/{quote(path)}",
        params={"ref": sha},
        headers={**_headers(repo), "Accept": "application/vnd.github.raw"},
        timeout=30,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.text.split("\n")


# A resolution shorter than this, or with no letter or digit, matches almost any file ("}",
# "return 0;", an empty block), so finding it in the merged file proves nothing.
MIN_CONFIRM_CHARS = 12


def confirmable(resolution: str) -> bool:
    want = list(normalize(resolution.split("\n")))
    text = "".join(want)
    return len(text) >= MIN_CONFIRM_CHARS and any(c.isalnum() for c in text)


def _contains(final: list[str], resolution: str) -> bool:
    """True only when a resolution long enough to be distinctive appears in the merged file.
    Empty and trivially short resolutions are never confirmed."""
    if not confirmable(resolution):
        return False
    want = list(normalize(resolution.split("\n")))
    have = list(normalize(final))
    return any(have[i : i + len(want)] == want for i in range(len(have) - len(want) + 1))


def sync_record(pr_url: str) -> dict | None:
    for d in sorted(RUNS.glob("*")) if RUNS.exists() else []:
        for e in read_jsonl(d, "evals"):
            if e.get("kind") == "sync" and e.get("pr_url") == pr_url:
                return e
    return None


def recorded(repo: str) -> list[dict]:
    p = HISTORY / f"{_slug(repo)}.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def record_outcome(pr_url: str) -> dict:
    rec = sync_record(pr_url)
    if rec is None:
        raise ValueError(f"no Forkward sync opened {pr_url}")
    info = pr_info(pr_url)
    if not info["merged"]:
        return {"pr": pr_url, "merged": False, "state": info["state"], "recorded": 0}
    repo = info["repo"]
    if any(r["pr"] == pr_url for r in recorded(repo)):
        return {"pr": pr_url, "merged": True, "recorded": 0, "note": "already recorded"}
    rows = []
    for h in rec.get("hunks", []):
        ok = confirmable(h["resolution"])
        final = file_at(repo, info["sha"], h["file"]) if ok else None
        confirmed = final is not None and _contains(final, h["resolution"])
        rows.append(
            {
                "pr": pr_url,
                "key": h.get("key"),
                "file": h["file"],
                "hunk_id": h["hunk_id"],
                "gate": h["gate"],
                "confirmable": ok,
                "confirmed": confirmed,
                "resolution": h["resolution"],
                "t": time.time(),
            }
        )
    HISTORY.mkdir(parents=True, exist_ok=True)
    with (HISTORY / f"{_slug(repo)}.jsonl").open("a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return {"pr": pr_url, "merged": True, "recorded": len(rows), **stats(repo)}


def stats(repo: str) -> dict:
    rows = recorded(repo)
    # Hunks too short to confirm count neither for nor against the bound.
    auto = [r for r in rows if r["gate"] == "auto_apply" and r.get("confirmable", True)]
    changed = sum(not r["confirmed"] for r in auto)
    return {
        "repo": repo,
        "hunks_recorded": len(rows),
        "confirmed": sum(r["confirmed"] for r in rows),
        "auto_applied": len(auto),
        "auto_applied_changed_by_reviewer": changed,
        "certified_bound": clopper_pearson_upper(changed, len(auto)) if auto else None,
    }


def rerere(repo: str) -> dict[str, list[str]]:
    """Confirmed resolutions keyed by conflict, for reuse in the next sync of this repository."""
    return {
        r["key"]: r["resolution"].split("\n")
        for r in recorded(repo)
        if r["confirmed"] and r.get("key") and confirmable(r["resolution"])
    }


def learn_pending() -> list[dict]:
    """Record outcomes for every Forkward PR that has been merged since it was opened."""
    out = []
    seen = set()
    for d in sorted(RUNS.glob("*")) if RUNS.exists() else []:
        for e in read_jsonl(d, "evals"):
            url = e.get("pr_url")
            if e.get("kind") != "sync" or not url or url in seen:
                continue
            seen.add(url)
            try:
                res = record_outcome(url)
                if res.get("recorded"):
                    out.append(res)
            except (httpx.HTTPError, ValueError) as err:
                out.append({"pr": url, "error": str(err)})
    return out
