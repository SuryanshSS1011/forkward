"""Forkward keeps long-lived forks in sync with upstream."""

from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    """Per-user state directory: FORKWARD_HOME, else ~/.forkward."""
    if os.environ.get("FORKWARD_HOME"):
        return Path(os.environ["FORKWARD_HOME"])
    return Path.home() / ".forkward"
