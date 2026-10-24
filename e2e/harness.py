"""End-to-end harness: a local fork and upstream with real conflicts, a fake GitHub API, and a
stub model. With these, a whole hosted sync runs on one machine with no network and no keys: the
web app inserts a job, the DBOS worker merges, resolves, pushes a forkward/* branch and opens a
"pull request", and a reviewer's accepted hunks are committed back to the branch.

Nothing here is used in production. `tests/test_e2e.py` drives it from pytest, and
`python -m e2e.serve` runs it for the browser walkthrough in `e2e/browser.py`.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

FORK = "acme/kernel-fork"
UPSTREAM = "stable/kernel"
UPSTREAM_REF = f"{UPSTREAM}@linux-6.6.y"

BASE = {
    "drivers/net/eth.c": """\
#include <linux/module.h>

static int eth_probe(struct device *dev)
{
	int ret;

	ret = eth_reset(dev);
	if (ret)
		return ret;
	return 0;
}

static void eth_remove(struct device *dev)
{
	eth_stop(dev);
}
""",
    "drivers/gpio/pins.c": """\
int pin_setup(void)
{
	int a = 1;
	int b = 2;
	return a + b;
}
""",
}

# The fork's own changes (the vendor side).
FORK_EDITS = {
    "drivers/net/eth.c": [
        ("\tret = eth_reset(dev);\n", "\tret = vendor_eth_reset(dev, VENDOR_FLAGS);\n"),
        ("\teth_stop(dev);\n", "\teth_stop(dev);\n\tvendor_power_off(dev);\n"),
    ],
    "drivers/gpio/pins.c": [("\tint a = 1;\n", "\tint v = 0;\n\tint a = 10;\n")],
}

# Upstream's fixes that touch the same lines.
UPSTREAM_EDITS = {
    "drivers/net/eth.c": [
        ("\tret = eth_reset(dev);\n", '\tret = eth_reset(dev);\n\tdev_dbg(dev, "reset\\n");\n'),
        ("\teth_stop(dev);\n", "\teth_stop_sync(dev);\n"),
    ],
    "drivers/gpio/pins.c": [("\tint a = 1;\n\tint b = 2;\n", "\tint a = 10;\n\tint b = 20;\n")],
}


def _git(cwd: Path, *args: str) -> str:
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "HOME": str(cwd),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def _edit(files: dict[str, str], edits: dict[str, list[tuple[str, str]]]) -> dict[str, str]:
    out = dict(files)
    for name, pairs in edits.items():
        for old, new in pairs:
            assert old in out[name], (name, old)
            out[name] = out[name].replace(old, new, 1)
    return out


def make_repos(root: Path) -> Path:
    """Bare repositories at root/<owner>/<name>.git, laid out like a git host. Returns root."""
    work = root / "_work"
    work.mkdir(parents=True)
    _git(work, "init", "-q", "-b", "main")
    _write(work, BASE)
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "base")
    _git(work, "checkout", "-q", "-b", "linux-6.6.y")
    _write(work, _edit(BASE, UPSTREAM_EDITS))
    _git(work, "commit", "-q", "-am", "upstream stable fixes")
    _git(work, "checkout", "-q", "main")
    _write(work, _edit(BASE, FORK_EDITS))
    _git(work, "commit", "-q", "-am", "vendor changes")
    for slug, branch in ((UPSTREAM, "linux-6.6.y"), (FORK, "main")):
        bare = root / f"{slug}.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        _git(root, "init", "-q", "--bare", "-b", "main", str(bare))
        _git(work, "push", "-q", str(bare), f"{branch}:{branch}")
    _git(root / f"{FORK}.git", "symbolic-ref", "HEAD", "refs/heads/main")
    return root


def branch_file(root: Path, slug: str, branch: str, path: str) -> str:
    return _git(root / f"{slug}.git", "show", f"{branch}:{path}")


def app_private_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


class FakeGitHub:
    """The handful of GitHub REST endpoints a sync uses, backed by the bare repositories."""

    def __init__(self, root: Path, installation: int = 77):
        self.root = root
        self.installation = installation
        self.pulls: list[dict] = []
        self.check_runs: list[dict] = []
        self.ci = "success"
        self.server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self, port: int = 0) -> FakeGitHub:
        gh = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                return None

            def _send(self, code: int, body: dict | list) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _body(self) -> dict:
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}")

            def do_GET(self):
                p = self.path.split("?")[0]
                if m := re.fullmatch(r"/repos/([^/]+/[^/]+)/installation", p):
                    return self._send(200, {"id": gh.installation})
                if m := re.fullmatch(r"/users/(.+)", p):
                    return self._send(200, {"id": 4242, "login": m.group(1)})
                if m := re.fullmatch(r"/repos/([^/]+/[^/]+)/branches/(.+)", p):
                    sha = _git(gh.root / f"{m.group(1)}.git", "rev-parse", m.group(2)).strip()
                    return self._send(200, {"name": m.group(2), "commit": {"sha": sha}})
                if re.fullmatch(r"/repos/[^/]+/[^/]+/commits/[0-9a-f]+/check-runs", p):
                    runs = [{"name": "build", "status": "completed", "conclusion": gh.ci}]
                    return self._send(200, {"check_runs": runs})
                if m := re.fullmatch(r"/repos/([^/]+/[^/]+)/pulls/(\d+)", p):
                    pr = gh.pulls[int(m.group(2)) - 1]
                    return self._send(200, pr)
                return self._send(404, {"message": f"fake GitHub has no GET {p}"})

            def do_POST(self):
                p, body = self.path.split("?")[0], self._body()
                if re.fullmatch(r"/app/installations/\d+/access_tokens", p):
                    return self._send(
                        201, {"token": "ghs_fake", "expires_at": "2099-01-01T00:00:00Z"}
                    )
                if m := re.fullmatch(r"/repos/([^/]+/[^/]+)/pulls", p):
                    n = len(gh.pulls) + 1
                    pr = {
                        **body,
                        "number": n,
                        "node_id": f"PR_{n}",
                        "html_url": f"https://github.com/{m.group(1)}/pull/{n}",
                    }
                    gh.pulls.append(pr)
                    return self._send(201, pr)
                if re.fullmatch(r"/repos/[^/]+/[^/]+/check-runs", p):
                    gh.check_runs.append(body)
                    return self._send(201, {"html_url": "https://github.com/check"})
                if p == "/graphql":
                    for pr in gh.pulls:
                        if pr["node_id"] == body["variables"]["id"]:
                            pr["draft"] = False
                    return self._send(200, {"data": {}})
                return self._send(404, {"message": f"fake GitHub has no POST {p}"})

        self.server = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()


_SIDES = re.compile(
    r"Vendor side of the conflict[^\n]*\n```\n(.*?)\n?```\nUpstream side of the conflict[^\n]*\n```\n(.*?)\n?```",
    re.DOTALL,
)


def stub_resolution(vendor: list[str], upstream: list[str]) -> tuple[list[str], str]:
    """A deterministic stand-in for a model: keep the vendor side when upstream adds nothing new,
    take upstream when it already contains the vendor side, and otherwise append upstream's new
    lines to the vendor side."""
    if not upstream or set(upstream) <= set(vendor):
        return vendor, "keep_vendor"
    if set(vendor) <= set(upstream):
        return upstream, "take_upstream"
    return vendor + [x for x in upstream if x not in vendor], "merge"


class StubModel:
    """An OpenAI-compatible chat endpoint that answers with `stub_resolution`."""

    def __init__(self):
        self.calls = 0
        self.server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def start(self, port: int = 0) -> StubModel:
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                return None

            def do_GET(self):
                data = json.dumps({"data": [{"id": "stub"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                req = json.loads(self.rfile.read(n))
                stub.calls += 1
                prompt = req["messages"][-1]["content"]
                m = _SIDES.search(prompt)
                vendor = m.group(1).split("\n") if m and m.group(1) else []
                upstream = m.group(2).split("\n") if m and m.group(2) else []
                lines, label = stub_resolution(vendor, upstream)
                if "Predict how the vendor's engineers resolved" in prompt:
                    text = label
                else:
                    text = (
                        "REASON: stub\nLESSONS: none\n```resolution\n" + "\n".join(lines) + "\n```"
                    )
                body = {
                    "id": "stub",
                    "model": req.get("model", "stub"),
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": text}}],
                    "usage": {"prompt_tokens": len(prompt) // 4, "completion_tokens": 20},
                }
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()


def environment(root: Path, github: FakeGitHub, model: StubModel, runs: Path) -> dict[str, str]:
    """Settings that point the worker at the harness. The stub model gets a calibration file with
    a 0.5 threshold so both gate outcomes occur; this is test scaffolding, not a certification."""
    from forkward import search, sync

    cal = runs / "calibrations"
    cal.mkdir(parents=True, exist_ok=True)
    strategy = json.loads(search.PACKAGED.read_text())["strategy"]["id"]
    path = cal / sync.calibration_path(strategy, "local", "stub").name
    path.write_text(json.dumps({"threshold": 0.5, "target_met": True}))
    return {
        "FORKWARD_GIT_URL": f"file://{root}",
        "GITHUB_API_URL": github.url,
        "GITHUB_APP_ID": "1",
        "GITHUB_APP_SLUG": "forkward-bot",
        "GITHUB_APP_PRIVATE_KEY": app_private_key().replace("\n", "\\n"),
        "LLM_PROVIDER": "local",
        "LOCAL_BASE_URL": model.url,
        "LOCAL_MODEL": "stub",
        "FORKWARD_RUNS": str(runs),
        "FORKWARD_CACHE": str(root.parent / "cache"),
        "FORKWARD_HOME": str(root.parent / "home"),
    }


def seed_tenant(tenant_id: str, installation: int = 77, repo_id: int = 501) -> None:
    """A tenant with the fork installed and its upstream set, as the web app's Setup URL records."""
    from forkward import db

    with db.system() as cur:
        cur.execute(
            "INSERT INTO tenants (id, name, plan) VALUES (%s, 'Acme', 'team') "
            "ON CONFLICT (id) DO NOTHING",
            (tenant_id,),
        )
        cur.execute(
            "INSERT INTO installations (id, tenant_id, account_login, account_type) "
            "VALUES (%s, %s, 'acme', 'Organization') ON CONFLICT (id) DO NOTHING",
            (installation, tenant_id),
        )
        cur.execute(
            "INSERT INTO repositories (id, installation_id, tenant_id, full_name, default_branch, "
            "upstream) VALUES (%s, %s, %s, %s, 'main', %s) ON CONFLICT (id) DO NOTHING",
            (repo_id, installation, tenant_id, FORK, UPSTREAM_REF),
        )
