"""Het lopen door de werkdirectory — de bron van waarheid voor `status` en `add`.

De ``.wit``-map zelf wordt overgeslagen; verder zijn het gewone, echte bestanden.
Paden worden als relatieve POSIX-paden t.o.v. de repository-root genormaliseerd.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from .ignore import LayeredIgnore
from .repo import WIT_DIR


def walk_files(
    base: Path,
    *,
    root: Path | None = None,
    ignore: LayeredIgnore | None = None,
) -> Iterator[Path]:
    """Alle bestanden onder ``base`` (recursief), met ``.wit`` gesnoeid.

    Een expliciet genoemd bestand wordt altijd opgeleverd. Tijdens het aflopen van een
    map worden, als ``root`` en ``ignore`` gegeven zijn, genegeerde mappen gesnoeid en
    genegeerde bestanden overgeslagen.
    """
    base = Path(base)
    if base.is_file():
        yield base
        return
    filtering = ignore is not None and root is not None

    def ignored(path: Path, is_dir: bool) -> bool:
        return filtering and ignore.match(rel_path(path, root), is_dir)  # type: ignore[union-attr,arg-type]

    for dirpath, dirnames, filenames in os.walk(base):
        keep = []
        for d in sorted(dirnames):
            if d == WIT_DIR:
                continue
            if ignored(Path(dirpath) / d, True):
                continue
            keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if not ignored(path, False):
                yield path


def rel_path(path: Path, root: Path) -> str:
    return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
