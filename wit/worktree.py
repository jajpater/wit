"""Het lopen door de werkdirectory — de bron van waarheid voor `status` en `add`.

De ``.wit``-map zelf wordt overgeslagen; verder zijn het gewone, echte bestanden.
Paden worden als relatieve POSIX-paden t.o.v. de repository-root genormaliseerd.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from .repo import WIT_DIR


def walk_files(base: Path) -> Iterator[Path]:
    """Alle bestanden onder ``base`` (recursief), met ``.wit`` gesnoeid."""
    base = Path(base)
    if base.is_file():
        yield base
        return
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != WIT_DIR]
        for name in sorted(filenames):
            yield Path(dirpath) / name


def rel_path(path: Path, root: Path) -> str:
    return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
