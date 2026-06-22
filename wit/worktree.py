"""Walking the working directory — the source of truth for `status` and `add`.

The ``.wit`` directory itself is skipped; otherwise they are ordinary, real files.
Paths are normalized as relative POSIX paths w.r.t. the repository root.
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
    """All files under ``base`` (recursively), with ``.wit`` pruned.

    An explicitly specified file is always yielded. During the traversal of a
    directory, if ``root`` and ``ignore`` are given, ignored directories are pruned and
    ignored files are skipped.

    Symlinks are skipped (not followed and not tracked): wit stores real file bytes
    and restores a working dir as real files, so a symlink has no representation.
    A dangling symlink — e.g. an editor lock file like ``.#foo.org`` — would
    otherwise crash ``add`` when its (non-existent) target is opened.
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
            full = Path(dirpath) / d
            if full.is_symlink():  # don't descend into or track symlinked dirs
                continue
            if ignored(full, True):
                continue
            keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.is_symlink():  # skip symlinks (incl. dangling editor locks)
                continue
            if not ignored(path, False):
                yield path


def rel_path(path: Path, root: Path) -> str:
    return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
