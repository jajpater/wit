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
from .repo import init, read_shallow
from .trees import read_tree


def _ancestors(
    store: ObjectStore, head: str | None, boundary: frozenset[str] = frozenset()
) -> set[str]:
    seen: set[str] = set()
    stack = [head] if head else []
    while stack:
        cid = stack.pop()
        if cid in seen:
            continue
        seen.add(cid)
        if cid not in boundary:  # retentie-grens: niet verder terug (parents zijn weg)
            # Een afwezige parent is een natuurlijke shallow-grens (lokaal of remote
            # geveegd door retentie); behandel hem als horizon i.p.v. te crashen.
            stack.extend(
                p for p in read_commit(store, cid)["parents"]
                if store.has("commits", p)
            )
    return seen


def _is_ancestor(
    store: ObjectStore,
    ancestor: str,
    descendant: str,
    boundary: frozenset[str] = frozenset(),
) -> bool:
    return ancestor in _ancestors(store, descendant, boundary)


def _reachable_tree(store: ObjectStore, tree_oid: str) -> Iterator[tuple[str, str]]:
    yield "trees", tree_oid
    for entry in read_tree(store, tree_oid).values():
        if entry["type"] == "tree":
            yield from _reachable_tree(store, entry["hash"])
        else:
            yield "blobs", entry["hash"]


def _reachable_objects(
    store: ObjectStore,
    head: str,
    have_commits: set[str],
    boundary: frozenset[str] = frozenset(),
) -> Iterator[tuple[str, str]]:
    """Alle (kind, oid) bereikbaar vanaf ``head``, exclusief reeds aanwezige commits.

    Stopt bij een retentie-grens (``boundary``): de grens-commit zelf en zijn tree
    worden nog meegenomen, maar zijn parents niet (die zijn lokaal weggeveegd).
    """
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
        if cid not in boundary:
            stack.extend(p for p in commit["parents"] if store.has("commits", p))


def push(wit: Path, store: ObjectStore, remote: Remote) -> str:
    local_head = read_head(wit)
    if local_head is None:
        raise ValueError("niets om te pushen (geen commits)")
    # Lokale retentie kan oudere commits hebben weggeveegd; stop de DAG-walk bij de grens
    # (de grens-commit refereert aan een parent die lokaal niet meer bestaat).
    boundary = frozenset(read_shallow(wit))
    remote_head = remote.read_ref(MAIN_REF)
    if remote_head is not None:
        if remote_head == local_head:
            return local_head  # up-to-date
        if not _is_ancestor(store, remote_head, local_head, boundary):
            raise ValueError("non-fast-forward: eerst pull (reconcile is M6)")

    have = _ancestors(store, remote_head, boundary)
    items = list(_reachable_objects(store, local_head, have, boundary))
    remote.upload_objects(store, items)  # M7: bulk i.p.v. per object

    # ref-CAS als laatste stap — de waarheidstransactie
    if not remote.compare_and_swap_ref(MAIN_REF, remote_head, local_head):
        raise ValueError("push afgewezen: remote-ref is intussen gewijzigd; eerst pull")
    return local_head


def fetch(store: ObjectStore, remote: Remote) -> str | None:
    """Download alle objecten bereikbaar vanaf de remote main; raak refs niet aan.

    Strategie (DOEL.md): haal eerst alle metadata (commits+trees) wholesale, bepaal
    dan lokaal welke blobs ontbreken, en haal die in bulk. Zo is het aantal
    transport-operaties constant i.p.v. evenredig met het aantal objecten.
    """
    remote_head = remote.read_ref(MAIN_REF)
    if remote_head is None:
        return None
    remote.fetch_metadata(store)  # alle commits + trees lokaal
    blobs = [
        (kind, oid)
        for kind, oid in _reachable_objects(store, remote_head, set())
        if kind == "blobs" and not store.has("blobs", oid)
    ]
    remote.download_objects(store, blobs)  # M7: bulk
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
