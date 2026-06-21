"""Commit-objecten en DAG-historie.

Een commit is ``{tree, parents[], time, message, host}`` (DOEL.md). ``parents`` is een
lijst: merge-commits (>= 2 parents) zijn vanaf het begin toegestaan, dus de historie is
een DAG. ``log`` loopt die DAG met een visited-set en ordent op tijd.
"""

from __future__ import annotations

import socket
from collections.abc import Iterable
from datetime import datetime, timezone

from .objects import ObjectStore
from .serialize import dumps, loads


def now_rfc3339() -> str:
    # Microsecondenprecisie zodat commits in dezelfde seconde toch verschillen.
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
    """Loop de commit-DAG vanaf ``head`` in topologische volgorde.

    Een commit verschijnt altijd vóór zijn parents (kind-vóór-ouder); tussen
    onafhankelijke takken bepaalt de tijd (nieuwste eerst, id als tiebreak). Zo is de
    volgorde correct, ook als twee commits exact dezelfde tijd hebben. Bij een
    ``shallow``-grens wordt niet verder teruggelopen (retentie).
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
            # Een afwezige parent (door retentie geveegd, hier of op een remote vanwaar
            # we shallow kloonden) is een impliciete grens: niet proberen te lezen.
            stack.extend(
                p for p in commits[cid]["parents"] if store.has("commits", p)
            )

    # Aantal kinderen binnen de bereikbare set: een commit is pas 'klaar' als al zijn
    # kinderen geëmitteerd zijn.
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
