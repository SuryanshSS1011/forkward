"""Run the end-to-end harness for a browser walkthrough of the web app on one machine.

    uv run python -m e2e.serve --tenant t_00000000000000de --dir /tmp/forkward-e2e

It builds the fork and upstream, starts the fake GitHub API (port 8790) and the stub model
(port 8791), installs the fork for the tenant (frontend/scripts/dev-session.mjs creates that
tenant), and writes the worker's settings to <dir>/worker.env. Start the worker with them:

    set -a; . ./.env; . <dir>/worker.env; set +a; uv run python -m forkward worker
"""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

from e2e import harness
from forkward import llm


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tenant", required=True)
    p.add_argument("--dir", default="/tmp/forkward-e2e")
    a = p.parse_args()
    llm.load_env()
    d = Path(a.dir)
    shutil.rmtree(d, ignore_errors=True)
    root = harness.make_repos(d / "git")
    gh = harness.FakeGitHub(root).start(8790)
    model = harness.StubModel().start(8791)
    env = harness.environment(root, gh, model, d / "runs")
    (d / "worker.env").write_text("".join(f"{k}='{v}'\n" for k, v in env.items()))
    harness.seed_tenant(a.tenant)
    print(f"harness ready: GitHub {gh.url}, model {model.url}, settings in {d / 'worker.env'}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
