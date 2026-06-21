"""`.witignore`: bepaalt welke bestanden buiten beheer blijven.

Ondersteund: commentaar (``#``), lege regels, glob-patronen (``fnmatch``), mappatronen
met afsluitende ``/``, en verankering met een leidende ``/``. Een patroon zonder ``/``
matcht op elk niveau (op een bestands- of mapnaam); een patroon met ``/`` is verankerd
aan de map waarin het ``.witignore`` staat. (Geen negatie of ``**`` — dat is later.)

``.witignore`` is genest: elke map mag er een hebben, en die regels gelden alleen voor de
subboom eronder (verankerde patronen relatief aan die map). Een `LayeredIgnore` bundelt
alle gevonden bestanden; bij het matchen telt elke laag waarvan de map een voorouder (of
de map zelf) van het pad is.

Net als bij git geldt ignore alleen voor *niet-gevolgde* bestanden: wat al in de index
staat blijft gevolgd, ook als het later een patroon matcht.
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
                    # matcht het pad zelf (indien dir) of een voorouder-map
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
    """Geneste ``.witignore``-regels: per map een ``IgnoreRules``, gestapeld op prefix."""

    def __init__(self, layers: dict[str, IgnoreRules]) -> None:
        # prefix "" = repo-root; "sub/dir/" = regels van .witignore in die map
        self.layers = layers

    def match(self, rel: str, is_dir: bool) -> bool:
        for prefix, rules in self.layers.items():
            if prefix == "":
                if rules.match(rel, is_dir):
                    return True
            elif rel.startswith(prefix):
                # match het pad relatief aan de map waarin dit .witignore staat
                if rules.match(rel[len(prefix):], is_dir):
                    return True
        return False


def load_ignore(root: Path) -> LayeredIgnore:
    """Verzamel alle ``.witignore``-bestanden onder ``root`` tot één gelaagde matcher."""
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
