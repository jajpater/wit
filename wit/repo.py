"""Repository layout: initialization and finding the ``.wit`` directory."""

from __future__ import annotations

import tomllib
from pathlib import Path

from .i18n import _

WIT_DIR = ".wit"

_LAYOUT = (
    "objects/blobs",
    "objects/trees",
    "objects/commits",
    "refs/heads",
    "tmp",
    "locks",
)

_CONFIG = 'object_format_version = 1\nhash = "blake3"\n'


def init(root: Path) -> Path:
    """Create an empty repository under ``root`` and return the ``.wit`` path."""
    wit = Path(root) / WIT_DIR
    if wit.exists():
        raise FileExistsError(_("{wit} already exists").format(wit=wit))
    for sub in _LAYOUT:
        (wit / sub).mkdir(parents=True)
    (wit / "HEAD").write_text("ref: refs/heads/main\n")
    (wit / "config.toml").write_text(_CONFIG)
    return wit


def find_wit(start: Path | None = None) -> Path:
    """Walk up from ``start`` (or cwd) until a ``.wit`` directory is found."""
    path = Path(start or Path.cwd()).resolve()
    for candidate in (path, *path.parents):
        wit = candidate / WIT_DIR
        if wit.is_dir():
            return wit
    raise FileNotFoundError(_("no wit repository found (.wit missing)"))


def read_config(wit: Path) -> dict:
    return tomllib.loads((Path(wit) / "config.toml").read_text())


def _write_config(wit: Path, cfg: dict) -> None:
    lines = [
        f'{k} = "{v}"' if isinstance(v, str) else f"{k} = {v}"
        for k, v in cfg.items()
    ]
    (Path(wit) / "config.toml").write_text("\n".join(lines) + "\n")


def set_remote(wit: Path, remote_path: str) -> None:
    cfg = read_config(wit)
    cfg["remote"] = str(remote_path)
    _write_config(wit, cfg)


def read_sparse(wit: Path) -> list[str]:
    """The sparse cone (path prefixes); empty = full checkout."""
    path = Path(wit) / "sparse"
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def set_sparse(wit: Path, patterns: list[str]) -> None:
    (Path(wit) / "sparse").write_text("\n".join(patterns) + "\n" if patterns else "")


def read_shallow(wit: Path) -> set[str]:
    """Commit-ids whose parents are considered absent (retention boundary)."""
    path = Path(wit) / "shallow"
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text().splitlines() if line.strip()}


def write_shallow(wit: Path, commit_ids: set[str]) -> None:
    text = "\n".join(sorted(commit_ids)) + "\n" if commit_ids else ""
    (Path(wit) / "shallow").write_text(text)


def head_commits(wit: Path) -> list[str]:
    """All commit-ids that the refs under refs/heads point to."""
    heads = Path(wit) / "refs" / "heads"
    return [p.read_text().strip() for p in heads.glob("*") if p.is_file()]


def sparse_includes(patterns: list[str], rel: str) -> bool:
    """Is ``rel`` in the cone? (Empty cone = everything included.)"""
    if not patterns:
        return True
    for pat in patterns:
        prefix = pat.rstrip("/")
        if rel == prefix or rel.startswith(prefix + "/"):
            return True
    return False
