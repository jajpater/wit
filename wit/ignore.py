"""`.witignore`: bepaalt welke bestanden buiten beheer blijven.

Ondersteund: commentaar (``#``), lege regels, glob-patronen (``fnmatch``), mappatronen
met afsluitende ``/``, en verankering met een leidende ``/``. Een patroon zonder ``/``
matcht op elk niveau (op een bestands- of mapnaam); een patroon met ``/`` matcht het
volledige pad vanaf de repository-root. (Geen negatie of ``**`` — dat is later.)

Net als bij git geldt ignore alleen voor *niet-gevolgde* bestanden: wat al in de index
staat blijft gevolgd, ook als het later een patroon matcht.
"""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatch
from pathlib import Path

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


def load_ignore(root: Path) -> IgnoreRules:
    path = Path(root) / IGNORE_FILE
    lines = path.read_text().splitlines() if path.exists() else []
    return IgnoreRules(lines)
