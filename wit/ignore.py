"""`.witignore`: determines which files remain untracked.

Supported: comments (``#``), empty lines, glob patterns (``fnmatch``), directory patterns
with trailing ``/``, and anchoring with a leading ``/``. A pattern without ``/``
matches at any level (on a file or directory name); a pattern with ``/`` is anchored
to the directory where the ``.witignore`` is located. (No negation or ``**`` — that's for later.)

``.witignore`` is nested: every directory can have one, and those rules only apply to the
subtree below it (anchored patterns relative to that directory). A `LayeredIgnore` bundles
all found files; when matching, every layer where the directory is an ancestor (or
the directory itself) of the path counts.

Just like with git, ignore only applies to *untracked* files: what is already in the index
remains tracked, even if it later matches a pattern.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from fnmatch import fnmatch
from pathlib import Path

from .repo import WIT_DIR

IGNORE_FILE = ".witignore"


class IgnoreRules:
    def __init__(self, patterns: Iterable[str]) -> None:
        # rule = (pattern, dir_only, anchored)
        self.rules: list[tuple[str, bool, bool]] = []
        for raw in patterns:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            dir_only = line.endswith("/")
            pat = line.rstrip("/")
            anchored = "/" in pat
            self.rules.append((pat.lstrip("/"), dir_only, anchored))

    def match(self, rel: str, is_dir: bool) -> bool:
        parts = rel.split("/")
        for pat, dir_only, anchored in self.rules:
            if anchored:
                if not dir_only and fnmatch(rel, pat):
                    return True
                if dir_only:
                    # matches the path itself (if dir) or an ancestor directory
                    bound = len(parts) + (1 if is_dir else 0)
                    if any(fnmatch("/".join(parts[:i]), pat) for i in range(1, bound)):
                        return True
            else:
                for i, part in enumerate(parts):
                    component_is_dir = is_dir or i != len(parts) - 1
                    if dir_only and not component_is_dir:
                        continue
                    if fnmatch(part, pat):
                        return True
        return False


class LayeredIgnore:
    """Nested ``.witignore`` rules: an ``IgnoreRules`` per directory, stacked by prefix."""

    def __init__(self, layers: dict[str, IgnoreRules]) -> None:
        # prefix "" = repo root; "sub/dir/" = rules from .witignore in that directory
        self.layers = layers

    def match(self, rel: str, is_dir: bool) -> bool:
        for prefix, rules in self.layers.items():
            if prefix == "":
                if rules.match(rel, is_dir):
                    return True
            elif rel.startswith(prefix):
                # match the path relative to the directory containing this .witignore
                if rules.match(rel[len(prefix):], is_dir):
                    return True
        return False


def load_ignore(root: Path) -> LayeredIgnore:
    """Collect all ``.witignore`` files under ``root`` into a single layered matcher."""
    root = Path(root)
    base = root.resolve()
    layers: dict[str, IgnoreRules] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != WIT_DIR]
        if IGNORE_FILE not in filenames:
            continue
        rel_dir = Path(dirpath).resolve().relative_to(base).as_posix()
        prefix = "" if rel_dir == "." else rel_dir + "/"
        lines = (Path(dirpath) / IGNORE_FILE).read_text().splitlines()
        layers[prefix] = IgnoreRules(lines)
    return LayeredIgnore(layers)
