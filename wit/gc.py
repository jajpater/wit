"""Garbage collection: mark -> grace -> sweep (DOEL.md).

Verwijdert objecten die vanuit geen enkele ref bereikbaar zijn. Nooit onmiddellijk: een
royaal vast grace-venster beschermt net-geschreven objecten tegen de GC<->push-race (een
multi-GB-push kan lang duren, dus het venster is niet "de push-duur" maar gewoon ruim).
De index telt óók als root, zodat staged-maar-nog-niet-gecommitte blobs blijven bestaan.

Beleid (DOEL.md): lokale GC is toegestaan; remote-GC op een smart server is later; op een
dumbe remote staat GC standaard uit. Dit is de lokale variant.
"""

from __future__ import annotations

import fcntl
import time
from dataclasses import dataclass
from pathlib import Path

from .commits import read_commit
from .index import Index
from .objects import KINDS, ObjectStore
from .trees import read_tree

DEFAULT_GRACE_SECONDS = 14 * 24 * 3600  # ~twee weken, vgl. git's gc.pruneExpire


@dataclass
class GcReport:
    removed: int = 0
    kept: int = 0
    skipped_young: int = 0


def _mark_tree(store: ObjectStore, tree_oid: str, reachable: set[tuple[str, str]]) -> None:
    key = ("trees", tree_oid)
    if key in reachable:
        return
    reachable.add(key)
    for entry in read_tree(store, tree_oid).values():
        if entry["type"] == "tree":
            _mark_tree(store, entry["hash"], reachable)
        else:
            reachable.add(("blobs", entry["hash"]))


def _mark(wit: Path, store: ObjectStore) -> set[tuple[str, str]]:
    reachable: set[tuple[str, str]] = set()
    # roots: alle refs onder refs/heads
    heads = wit / "refs" / "heads"
    roots = [p.read_text().strip() for p in heads.glob("*") if p.is_file()]
    stack = list(roots)
    while stack:
        cid = stack.pop()
        if ("commits", cid) in reachable:
            continue
        reachable.add(("commits", cid))
        commit = read_commit(store, cid)
        _mark_tree(store, commit["tree"], reachable)
        stack.extend(commit["parents"])
    # roots: de index (staged, nog niet gecommit)
    with Index(wit) as index:
        for entry in index.entries():
            reachable.add(("blobs", entry.hash))
    return reachable


def gc(
    wit: Path, store: ObjectStore, grace_seconds: float = DEFAULT_GRACE_SECONDS
) -> GcReport:
    locks = wit / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    with open(locks / "gc.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            reachable = _mark(wit, store)
            report = GcReport()
            now = time.time()
            for kind in KINDS:
                for oid in list(store.iter_objects(kind)):
                    if (kind, oid) in reachable:
                        report.kept += 1
                        continue
                    path = store.path_for(kind, oid)
                    if now - path.stat().st_mtime < grace_seconds:
                        report.skipped_young += 1  # grace: te jong om te vegen
                        continue
                    path.unlink()
                    report.removed += 1
            return report
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
