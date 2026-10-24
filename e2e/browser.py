"""Browser walkthrough of the web app against a running local stack.

Start Postgres, the Next.js app, the harness (`python -m e2e.serve`) and the worker, create a
session with `node frontend/scripts/dev-session.mjs > cookie.txt`, then:

    uv run --with playwright python -m e2e.browser cookie.txt shots/

It checks that signed-out visitors see the public site and are sent to sign in from the
workspace, and that signed-in people land in the workspace. It signs in with the cookie, previews
and syncs the harness fork from the repositories page,
accepts and edits a hunk, commits it, walks the sample run, and checks every page loads without
console errors. Screenshots land in the given directory. Exits non-zero on the first failure.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("FORKWARD_WEB", "http://localhost:3000")
CHROME = os.environ.get("CHROME", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
PAGES = [
    "/",
    "/demo",
    "/demo/live",
    "/repos",
    "/monitor",
    "/connect",
    "/keys",
    "/billing",
    "/signin",
]


def main() -> int:
    cookie_file, shots = Path(sys.argv[1]), Path(sys.argv[2])
    shots.mkdir(parents=True, exist_ok=True)
    name, value = cookie_file.read_text().strip().split("=", 1)
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME if Path(CHROME).exists() else None)

        def page_for(signed_in: bool, width: int = 1280):
            ctx = browser.new_context(viewport={"width": width, "height": 900})
            if signed_in:
                ctx.add_cookies([{"name": name, "value": value, "url": BASE}])
            pg = ctx.new_page()
            # Failed requests are reported by URL, which says more than the console line does.
            pg.on(
                "response",
                lambda r: (
                    problems.append(f"{pg.url}: {r.status} {r.url}") if r.status >= 400 else None
                ),
            )
            pg.on(
                "console",
                lambda m: (
                    problems.append(f"{pg.url}: {m.text[:200]}")
                    if m.type == "error" and "Failed to load resource" not in m.text
                    else None
                ),
            )
            pg.on("pageerror", lambda e: problems.append(f"{pg.url}: page error {e}"))
            return pg

        # Every page, signed out and in, without console errors.
        for signed in (False, True):
            pg = page_for(signed)
            for path in PAGES:
                pg.goto(BASE + path, wait_until="networkidle")
            pg.context.close()

        # Signed out: the workspace pages send you to sign in and the nav shows the public site.
        pg = page_for(False)
        pg.goto(f"{BASE}/repos", wait_until="networkidle")
        expect(pg).to_have_url(f"{BASE}/signin?next=%2Frepos")
        nav = pg.get_by_role("navigation", name="Main")
        expect(nav.get_by_role("link", name="Demo")).to_be_visible()
        expect(nav.get_by_role("link", name="Repositories")).to_have_count(0)
        pg.context.close()

        # Signed in: the landing page hands over to the workspace and the nav drops the demo.
        pg = page_for(True)
        pg.goto(BASE + "/", wait_until="networkidle")
        expect(pg).to_have_url(f"{BASE}/repos")
        nav = pg.get_by_role("navigation", name="Main")
        expect(nav.get_by_role("link", name="Activity")).to_be_visible()
        expect(nav.get_by_role("link", name="Demo")).to_have_count(0)
        pg.goto(f"{BASE}/repos", wait_until="networkidle")
        expect(pg.get_by_text("kernel-fork")).to_be_visible()
        pg.screenshot(path=shots / "repos.png", full_page=True)

        # Preview: nothing pushed, ends with a summary.
        pg.get_by_role("button", name="Preview").first.click()
        pg.wait_for_url("**/jobs/*")
        expect(pg.get_by_role("heading", level=1)).to_contain_text("would need you", timeout=60_000)
        pg.screenshot(path=shots / "preview.png", full_page=True)

        # Sync: opens a PR, waits for review.
        pg.goto(f"{BASE}/repos", wait_until="networkidle")
        pg.get_by_role("button", name="Sync now").first.click()
        pg.wait_for_url("**/jobs/*")
        expect(pg.get_by_role("heading", level=1)).to_contain_text("needs you", timeout=60_000)
        expect(pg.get_by_role("link", name="Open the pull request")).to_be_visible()
        pg.screenshot(path=shots / "sync-review.png", full_page=True)

        # Edit, accept and commit the hunk that needs review.
        box = pg.get_by_role("textbox", name="Resolution")
        box.fill(box.input_value() + "\n\tint checked = a + b;")
        pg.get_by_role("button", name="Accept with my edit").click()
        commit = pg.get_by_role("button", name="Commit 1 accepted hunk to the branch")
        expect(commit).to_be_visible(timeout=15_000)
        commit.click()
        expect(pg.get_by_role("heading", level=1)).to_contain_text(
            "1 resolved by you. Ready to merge.", timeout=60_000
        )
        pg.get_by_text("What happened").click()
        expect(pg.get_by_text("and the pull request is ready to merge")).to_be_visible()
        pg.screenshot(path=shots / "sync-committed.png", full_page=True)

        # Activity lists both runs.
        pg.goto(f"{BASE}/monitor", wait_until="networkidle")
        expect(pg.get_by_text("Preview", exact=True).first).to_be_visible()
        pg.screenshot(path=shots / "activity.png", full_page=True)

        # Sample run: summary first, replay on request, review locally.
        pg.goto(f"{BASE}/demo/live", wait_until="networkidle")
        expect(pg.get_by_role("heading", level=1)).to_contain_text("4 need you")
        pg.screenshot(path=shots / "sample.png", full_page=True)
        pg.get_by_role("button", name="Replay the run").click()
        pg.wait_for_timeout(2600)
        pg.screenshot(path=shots / "sample-replay.png")
        expect(pg.get_by_role("heading", level=1)).to_contain_text("4 need you", timeout=15_000)
        pg.get_by_role("button", name="Accept", exact=True).click()
        pg.get_by_role("button", name="Commit 1 accepted hunk (sample, nothing is pushed)").click()
        expect(pg.get_by_role("heading", level=1)).to_contain_text("3 need you")
        pg.context.close()

        # Phone width.
        pg = page_for(True, width=390)
        pg.goto(f"{BASE}/demo/live", wait_until="networkidle")
        pg.screenshot(path=shots / "sample-phone.png", full_page=True)
        overflow = pg.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        if overflow:
            problems.append("/demo/live scrolls sideways at 390px")
        browser.close()

    for line in problems:
        print("PROBLEM", line)
    print("ok" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
