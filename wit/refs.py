"""Refs en HEAD.

De ref-update is de waarheidstransactie (DOEL.md): pas als ``refs/heads/main`` naar een
nieuwe commit wijst, bestaat die toestand. Lokaal schrijven we de ref atomair (tmp +
rename); de atomaire *compare-and-swap* over een remote is M6.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def head_ref(wit: Path) -> str:
    """De ref waar HEAD naar wijst, bv. ``refs/heads/main``."""
    content = (Path(wit) / "HEAD").read_text().strip()
    return content[5:].strip() if content.startswith("ref: ") else content


def read_ref(wit: Path, ref: str) -> str | None:
    path = Path(wit) / ref
    return path.read_text().strip() if path.exists() else None


def read_head(wit: Path) -> str | None:
    """De commit-id waar HEAD op staat, of ``None`` als er nog geen commits zijn."""
    return read_ref(wit, head_ref(wit))


def update_ref(wit: Path, ref: str, commit_id: str) -> None:
    wit = Path(wit)
    dest = wit / ref
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = wit / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=tmp_dir)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(commit_id + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, dest)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
