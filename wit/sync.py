"""Synchronisatie met een remote: fetch, push, pull, clone.

De ref-update is de waarheidstransactie: push uploadt eerst alle ontbrekende objecten en
zet pas daarna de ref via compare-and-swap (DOEL.md). M5a doet alleen fast-forward;
divergente historie samenvoegen (reconcile) is M6.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from .commits import read_commit
from .merge import reconcile
from .objects import ObjectStore
from .porcelain import checkout
from .refs import head_ref, read_head, update_ref
from .remote import MAIN_REF, Remote
from .repo import init
from .trees import read_tree


def _ancestors(store: ObjectStore, head: str | None) -> set[str]:
    seen: set[str] = set()
    stack = [head] if head else []
    while stack:
        cid = stack.pop()
        if cid in seen:
            continue
        seen.add(cid)
        stack.extend(read_commit(store, cid)["parents"])
    return seen


def _is_ancestor(store: ObjectStore, ancestor: str, descendant: str) -> bool:
    return ancestor in _ancestors(store, descendant)


def _reachable_tree(store: ObjectStore, tree_oid: str) -> Iterator[tuple[str, str]]:
    yield "trees", tree_oid
    for entry in read_tree(store, tree_oid).values():
        if entry["type"] == "tree":
            yield from _reachable_tree(store, entry["hash"])
        else:
            yield "blobs", entry["hash"]


def _reachable_objects(
    store: ObjectStore, head: str, have_commits: set[str]
) -> Iterator[tuple[str, str]]:
    """Alle (kind, oid) bereikbaar vanaf ``head``, exclusief reeds aanwezige commits."""
    seen: set[str] = set()
    stack = [head]
    while stack:
        cid = stack.pop()
        if cid in seen or cid in have_commits:
            continue
        seen.add(cid)
        yield "commits", cid
        commit = read_commit(store, cid)
        yield from _reachable_tree(store, commit["tree"])
        stack.extend(commit["parents"])


def push(wit: Path, store: ObjectStore, remote: Remote) -> str:
    local_head = read_head(wit)
    if local_head is None:
        raise ValueError("niets om te pushen (geen commits)")
    remote_head = remote.read_ref(MAIN_REF)
    if remote_head is not None:
        if remote_head == local_head:
            return local_head  # up-to-date
        if not _is_ancestor(store, remote_head, local_head):
            raise ValueError("non-fast-forward: eerst pull (reconcile is M6)")

    have = _ancestors(store, remote_head)
    for kind, oid in _reachable_objects(store, local_head, have):
        if not remote.has(kind, oid):
            remote.upload(store, kind, oid)

    # ref-CAS als laatste stap — de waarheidstransactie
    if not remote.compare_and_swap_ref(MAIN_REF, remote_head, local_head):
        raise ValueError("push afgewezen: remote-ref is intussen gewijzigd; eerst pull")
    return local_head


def _fetch_tree(store: ObjectStore, remote: Remote, tree_oid: str) -> None:
    if not store.has("trees", tree_oid):
        remote.download(store, "trees", tree_oid)
    for entry in read_tree(store, tree_oid).values():
        if entry["type"] == "tree":
            _fetch_tree(store, remote, entry["hash"])
        elif not store.has("blobs", entry["hash"]):
            remote.download(store, "blobs", entry["hash"])


def fetch(store: ObjectStore, remote: Remote) -> str | None:
    """Download alle objecten bereikbaar vanaf de remote main; raak refs niet aan."""
    remote_head = remote.read_ref(MAIN_REF)
    if remote_head is None:
        return None
    seen: set[str] = set()
    stack = [remote_head]
    while stack:
        cid = stack.pop()
        if cid in seen:
            continue
        seen.add(cid)
        if not store.has("commits", cid):
            remote.download(store, "commits", cid)
        commit = read_commit(store, cid)
        _fetch_tree(store, remote, commit["tree"])
        stack.extend(commit["parents"])
    return remote_head


def pull(wit: Path, store: ObjectStore, remote: Remote) -> tuple[str, list[str]] | None:
    """Haal de remote op; fast-forward of, bij divergentie, reconcile tot een merge.

    Geeft (head, conflict-paden) terug, of None als de remote leeg is.
    """
    head = fetch(store, remote)
    if head is None:
        return None
    local = read_head(wit)
    if local == head:
        return head, []  # up-to-date
    if local is None or _is_ancestor(store, local, head):
        update_ref(wit, head_ref(wit), head)  # fast-forward
        checkout(wit, store, head)
        return head, []
    if _is_ancestor(store, head, local):
        return local, []  # lokaal is al verder; niets te doen
    # divergent -> samenvoegen tot een merge-commit (geen historieverlies)
    return reconcile(wit, store, local, head)


def clone(remote: Remote, dest: Path) -> Path:
    wit = init(dest)
    store = ObjectStore(wit)
    head = fetch(store, remote)
    if head is not None:
        update_ref(wit, head_ref(wit), head)
        checkout(wit, store, head)
    return wit
