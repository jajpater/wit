"""Tree objects: the directory structure, built from the index.

A tree maps names to entries (``type``/``hash``, plus ``mode``/``size`` for blobs).
Unchanged subdirectories get the same hash and are reused across commits
— the tree/commit split is load-bearing for dedup (DOEL.md).
"""

from __future__ import annotations

from collections.abc import Iterable

from .index import IndexEntry
from .objects import ObjectStore
from .serialize import dumps, loads

DIR_MODE = 0o040000


def build_tree(entries: Iterable[IndexEntry], store: ObjectStore) -> str:
    """Build nested tree objects from flat index entries; return the root tree ID.

    A build-node is a ``dict`` where a value is either a nested ``dict``
    (subdirectory) or a leaf ``IndexEntry`` (file).
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
