"""Repository-layout: initialiseren en het vinden van de ``.wit``-map."""

from __future__ import annotations

import tomllib
from pathlib import Path

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
    """Maak een lege repository onder ``root`` en geef het ``.wit``-pad terug."""
    wit = Path(root) / WIT_DIR
    if wit.exists():
        raise FileExistsError(f"{wit} bestaat al")
    for sub in _LAYOUT:
        (wit / sub).mkdir(parents=True)
    (wit / "HEAD").write_text("ref: refs/heads/main\n")
    (wit / "config.toml").write_text(_CONFIG)
    return wit


def find_wit(start: Path | None = None) -> Path:
    """Loop omhoog vanaf ``start`` (of cwd) tot een ``.wit``-map gevonden is."""
    path = Path(start or Path.cwd()).resolve()
    for candidate in (path, *path.parents):
        wit = candidate / WIT_DIR
        if wit.is_dir():
            return wit
    raise FileNotFoundError("geen wit-repository gevonden (.wit ontbreekt)")


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
