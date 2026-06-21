"""Reconcile: divergente historie samenvoegen tot een merge-commit.

3-way merge op het manifest/tree-niveau (nooit op bytes van binaire documenten), met de
merge-base als basis. Een pad dat beide kanten verschillend wijzigden levert een conflict
op; dat wordt opgelost met *keep-both*: beide versies blijven als echte bestanden bestaan,
de binnenkomende onder ``pad.conflict-<host>-<commit>.ext``. De merge-commit krijgt twee
parents, zodat geen historie verloren gaat (DOEL.md).
"""

from __future__ import annotations

from collections import deque
from pathlib import Path, PurePosixPath

from .commits import create_commit, read_commit
from .index import Index, IndexEntry
from .objects import ObjectStore
from .porcelain import checkout
from .refs import head_ref, update_ref
from .trees import build_tree, read_tree


def tree_entries(store: ObjectStore, tree_oid: str, prefix: str = "") -> dict[str, dict]:
    """Plat ``pad -> tree-entry`` (met hash/mode/size) van een tree."""
    out: dict[str, dict] = {}
    for name, entry in read_tree(store, tree_oid).items():
        rel = f"{prefix}{name}"
        if entry["type"] == "tree":
            out.update(tree_entries(store, entry["hash"], rel + "/"))
        else:
            out[rel] = entry
    return out


def _ancestors_inclusive(store: ObjectStore, commit_id: str) -> set[str]:
    seen: set[str] = set()
    stack = [commit_id]
    while stack:
        cid = stack.pop()
        if cid in seen:
            continue
        seen.add(cid)
        stack.extend(read_commit(store, cid)["parents"])
    return seen


def merge_base(store: ObjectStore, a: str, b: str) -> str | None:
    """De dichtstbijzijnde gemeenschappelijke voorouder (LCA) van ``a`` en ``b``."""
    anc_a = _ancestors_inclusive(store, a)
    seen: set[str] = set()
    queue = deque([b])
    while queue:
        cid = queue.popleft()
        if cid in seen:
            continue
        seen.add(cid)
        if cid in anc_a:
            return cid
        queue.extend(read_commit(store, cid)["parents"])
    return None


def _conflict_name(path: str, host: str, commit_id: str) -> str:
    p = PurePosixPath(path)
    short = commit_id.split(":", 1)[1][:8]
    return str(p.with_name(f"{p.stem}.conflict-{host}-{short}{p.suffix}"))


def reconcile(
    wit: Path, store: ObjectStore, ours: str, theirs: str
) -> tuple[str, list[str]]:
    """Voeg ``theirs`` samen in ``ours`` tot een merge-commit; checkout het resultaat.

    Geeft (merge-commit-id, lijst van conflict-paden) terug.
    """
    base = merge_base(store, ours, theirs)
    base_map = tree_entries(store, read_commit(store, base)["tree"]) if base else {}
    ours_map = tree_entries(store, read_commit(store, ours)["tree"])
    theirs_map = tree_entries(store, read_commit(store, theirs)["tree"])
    theirs_host = read_commit(store, theirs)["host"]

    merged: dict[str, dict] = {}
    conflicts: list[str] = []

    def h(entry: dict | None) -> str | None:
        return entry["hash"] if entry else None

    for path in set(base_map) | set(ours_map) | set(theirs_map):
        b, o, t = base_map.get(path), ours_map.get(path), theirs_map.get(path)
        if h(o) == h(t):
            if o is not None:
                merged[path] = o
        elif h(o) == h(b):
            if t is not None:          # alleen theirs wijzigde
                merged[path] = t
        elif h(t) == h(b):
            if o is not None:          # alleen ours wijzigde
                merged[path] = o
        else:
            # beide kanten verschillend -> conflict, keep-both (geen dataverlies)
            conflicts.append(path)
            if o is not None and t is not None:
                merged[path] = o
                merged[_conflict_name(path, theirs_host, theirs)] = t
            elif o is not None:
                merged[path] = o
            elif t is not None:
                merged[path] = t

    tree = build_tree(
        [
            IndexEntry(p, e["hash"], e["mode"], e["size"], 0, 0, 0, 0)
            for p, e in merged.items()
        ],
        store,
    )
    message = f"merge {theirs.split(':', 1)[1][:8]} in {ours.split(':', 1)[1][:8]}"
    merge_commit = create_commit(store, tree, [ours, theirs], message)
    update_ref(wit, head_ref(wit), merge_commit)
    checkout(wit, store, merge_commit)
    # Checkout herbouwt de index; markeer daarna de open conflicten zodat `status` ze
    # toont tot de gebruiker ze oplost (kiezen, bewerken, `add`).
    if conflicts:
        with Index(wit) as index:
            for path in conflicts:
                index.mark_conflict(path)
    return merge_commit, conflicts
