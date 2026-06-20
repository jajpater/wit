"""Tree-objecten: de directorystructuur, opgebouwd uit de index.

Een tree mapt namen naar entries (``type``/``hash``, plus ``mode``/``size`` voor blobs).
Ongewijzigde subdirectories krijgen dezelfde hash en worden over commits heen hergebruikt
— de tree/commit-splitsing is dragend voor dedup (DOEL.md).
"""

from __future__ import annotations

from collections.abc import Iterable

from .index import IndexEntry
from .objects import ObjectStore
from .serialize import dumps, loads

DIR_MODE = 0o040000


def build_tree(entries: Iterable[IndexEntry], store: ObjectStore) -> str:
    """Bouw geneste tree-objecten uit platte index-entries; geef de root-tree-id terug.

    Een opbouw-knooppunt is een ``dict`` waarin een waarde óf een geneste ``dict``
    (subdirectory) óf een blad-``IndexEntry`` (bestand) is.
    """
    root: dict = {}
    for entry in entries:
        parts = entry.path.split("/")
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = entry
    return _write(root, store)


def _write(node: dict, store: ObjectStore) -> str:
    out: dict[str, dict[str, object]] = {}
    for name, child in node.items():
        if isinstance(child, IndexEntry):
            out[name] = {
                "type": "blob", "hash": child.hash,
                "mode": child.mode, "size": child.size,
            }
        else:
            out[name] = {"type": "tree", "hash": _write(child, store), "mode": DIR_MODE}
    return store.put("trees", dumps({"entries": out}))


def read_tree(store: ObjectStore, oid: str) -> dict[str, dict[str, object]]:
    return loads(store.get("trees", oid))["entries"]
