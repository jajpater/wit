"""Commit objects and DAG history.

A commit is ``{tree, parents[], time, message, host}`` (DOEL.md). ``parents`` is a
list: merge commits (>= 2 parents) are allowed from the start, so the history is
a DAG. ``log`` walks this DAG with a visited-set and orders by time.
"""

from __future__ import annotations

import socket
from collections.abc import Iterable
from datetime import datetime, timezone

from .objects import ObjectStore
from .serialize import dumps, loads


def now_rfc3339() -> str:
    # Microsecond precision so commits in the same second still differ.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def create_commit(
    store: ObjectStore,
    tree: str,
    parents: Iterable[str],
    message: str,
    *,
    time: str | None = None,
    host: str | None = None,
) -> str:
    obj = {
        "tree": tree,
        "parents": list(parents),
        "time": time or now_rfc3339(),
        "message": message,
        "host": host or socket.gethostname(),
    }
    return store.put("commits", dumps(obj))


def read_commit(store: ObjectStore, oid: str) -> dict:
    return loads(store.get("commits", oid))


def log(
    store: ObjectStore, head: str | None, shallow: set[str] | None = None
) -> list[tuple[str, dict]]:
    """Walk the commit-DAG from ``head`` in topological order.

    A commit always appears before its parents (child-before-parent); between
    independent branches, time decides (newest first, id as tiebreak). This ensures
    the order is correct, even if two commits have the exact same time. At a
    ``shallow`` boundary, it does not walk further back (retention).
    """
    if head is None:
        return []

    boundary = shallow or set()
    commits: dict[str, dict] = {}
    stack = [head]
    while stack:
        cid = stack.pop()
        if cid in commits:
            continue
        commits[cid] = read_commit(store, cid)
        if cid not in boundary:
            # An absent parent (swept by retention, here or on a remote from which
            # we shallow cloned) is an implicit boundary: don't try to read it.
            stack.extend(
                p for p in commits[cid]["parents"] if store.has("commits", p)
            )

    # Number of children within the reachable set: a commit is only 'ready' when all its
    # children have been emitted.
    remaining = {cid: 0 for cid in commits}
    for commit in commits.values():
        for parent in commit["parents"]:
            if parent in remaining:
                remaining[parent] += 1

    ready = {cid for cid, n in remaining.items() if n == 0}
    out: list[tuple[str, dict]] = []
    while ready:
        cid = max(ready, key=lambda c: (commits[c]["time"], c))
        ready.remove(cid)
        out.append((cid, commits[cid]))
        for parent in commits[cid]["parents"]:
            if parent in remaining:
                remaining[parent] -= 1
                if remaining[parent] == 0:
                    ready.add(parent)
    return out
