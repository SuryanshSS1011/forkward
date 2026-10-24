"""Archive the run logs so results/ can be checked and rebuilt elsewhere.

`python -m forkward bundle-runs` writes a gzipped tarball of runs/ with MANIFEST.sha256 inside
(one `<sha256>  <path>` line per file, the format `sha256sum -c` reads) and the same manifest
next to the tarball. File order, timestamps and owners are fixed, so the same runs give a
byte-identical tarball.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
import time
from pathlib import Path

from forkward.store import RUNS


def _files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def manifest(root: Path) -> str:
    lines = [
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  runs/{p.relative_to(root).as_posix()}"
        for p in _files(root)
    ]
    return "\n".join(lines) + "\n"


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = 0
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(data))


def bundle(root: Path = RUNS, out: Path | None = None) -> tuple[Path, Path]:
    """Write the tarball and its manifest. Refuses an empty or missing runs/ directory."""
    files = _files(root) if root.exists() else []
    if not files:
        raise FileNotFoundError(f"no run logs under {root}; nothing to bundle")
    out = out or Path(f"runs-bundle-{time.strftime('%Y%m%dT%H%M%S')}.tar.gz")
    text = manifest(root)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        _add(tar, "MANIFEST.sha256", text.encode())
        for p in files:
            _add(tar, f"runs/{p.relative_to(root).as_posix()}", p.read_bytes())
    with out.open("wb") as raw, gzip.GzipFile("", "wb", fileobj=raw, mtime=0) as gz:
        gz.write(buf.getvalue())
    side = out.with_name(out.name.removesuffix(".tar.gz") + ".sha256")
    side.write_text(text + f"{hashlib.sha256(out.read_bytes()).hexdigest()}  {out.name}\n")
    return out, side


def main(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="forkward bundle-runs")
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args(argv)
    try:
        tar, side = bundle(RUNS, a.out)
    except FileNotFoundError as e:
        print(e)
        return 1
    print(f"wrote {tar} and {side}")
    return 0
