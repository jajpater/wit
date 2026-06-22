"""Garbage collection: mark -> grace -> sweep (DOEL.md).

Removes objects that are not reachable from any ref. Never immediately: a
generous fixed grace window protects newly written objects against the GC<->push race (a
multi-GB push can take a long time, so the window isn't "the push duration" but just large).
The index also counts as a root, so staged-but-not-yet-committed blobs are preserved.

Policy (DOEL.md): local GC is allowed; remote-GC on a smart server is for later; on a
dumb remote, GC is off by default. This is the local variant.
"""

from __future__ import annotations

import fcntl
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .commits import read_commit
from .index import Index
from .objects import KINDS, ObjectStore
from .repo import read_shallow
from .trees import read_tree

DEFAULT_GRACE_SECONDS = 14 * 24 * 3600  # ~two weeks, cf. git's gc.pruneExpire


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


def refs_in(refs_dir: Path) -> list[str]:
    """The commit-ids that the heads under ``refs_dir`` point to (GC-roots)."""
    heads = refs_dir / "heads"
    if not heads.exists():
        return []
    return [p.read_text().strip() for p in heads.glob("*") if p.is_file()]


def mark_reachable(
    store: ObjectStore, roots: Iterable[str], boundary: frozenset[str] = frozenset()
) -> set[tuple[str, str]]:
    """Walk the commit-DAG from ``roots`` and collect all reachable objects.

    At a commit in ``boundary`` (retention boundary), we don't descend to its parents.
    Works on a bare ``ObjectStore`` + roots, so usable for both the local repo
    and a remote (smart-server GC).
    """
    reachable: set[tuple[str, str]] = set()
    stack = list(roots)
    while stack:
        cid = stack.pop()
        if ("commits", cid) in reachable:
            continue
        reachable.add(("commits", cid))
        commit = read_commit(store, cid)
        _mark_tree(store, commit["tree"], reachable)
        if cid not in boundary:
            # Absent parent (retention here or on a shallow-cloned remote) = boundary.
            stack.extend(p for p in commit["parents"] if store.has("commits", p))
    return reachable


def sweep(
    store: ObjectStore,
    reachable: set[tuple[str, str]],
    grace_seconds: float = DEFAULT_GRACE_SECONDS,
) -> GcReport:
    """Remove unreachable objects older than the grace window."""
    report = GcReport()
    now = time.time()
    for kind in KINDS:
        for oid in list(store.iter_objects(kind)):
            if (kind, oid) in reachable:
                report.kept += 1
                continue
            path = store.path_for(kind, oid)
            if now - path.stat().st_mtime < grace_seconds:
                report.skipped_young += 1  # grace: too young to sweep
                continue
            path.unlink()
            report.removed += 1
    return report


def _mark(wit: Path, store: ObjectStore) -> set[tuple[str, str]]:
    shallow = read_shallow(wit)  # at a boundary we don't descend to parents
    reachable = mark_reachable(store, refs_in(wit / "refs"), frozenset(shallow))
    # roots: the index (staged, not yet committed)
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
            return sweep(store, _mark(wit, store), grace_seconds)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
