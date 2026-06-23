"""Synchronization with a remote: fetch, push, pull, clone.

The ref-update is the true transaction: push uploads all missing objects first and
only then updates the ref via compare-and-swap. M5a only does fast-forward;
merging divergent history (reconcile) is M6.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from .commits import read_commit
from .i18n import _
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
        if cid not in boundary:  # retention boundary: no further back (parents are gone)
            # An absent parent is a natural shallow boundary (swept locally or remote
            # by retention); treat it as a horizon instead of crashing.
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
    """All (kind, oid) reachable from ``head``, excluding already present commits.

    Stops at a retention boundary (``boundary``): the boundary commit itself and its tree
    are still included, but its parents are not (they have been swept locally).
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
        raise ValueError(_("nothing to push (no commits)"))
    remote.prepare_push()  # hub: auto-create the repo on first push
    # Local retention might have swept older commits; stop the DAG-walk at the boundary
    # (the boundary commit refers to a parent that no longer exists locally).
    boundary = frozenset(read_shallow(wit))
    remote_head = remote.read_ref(MAIN_REF)
    if remote_head is not None:
        if remote_head == local_head:
            return local_head  # up-to-date
        if not _is_ancestor(store, remote_head, local_head, boundary):
            raise ValueError(_("non-fast-forward: pull first (reconcile is M6)"))

    have = _ancestors(store, remote_head, boundary)
    items = list(_reachable_objects(store, local_head, have, boundary))
    remote.upload_objects(store, items)  # M7: bulk i.p.v. per object

    # ref-CAS as the last step — the true transaction
    if not remote.compare_and_swap_ref(MAIN_REF, remote_head, local_head):
        raise ValueError(_("push rejected: remote-ref has changed; pull first"))
    return local_head


def fetch(store: ObjectStore, remote: Remote) -> str | None:
    """Download all objects reachable from remote main; do not touch refs.

    Strategy: fetch all metadata (commits+trees) wholesale first, then
    determine locally which blobs are missing, and fetch those in bulk. This keeps
    the number of transport operations constant instead of proportional to the object count.
    """
    remote_head = remote.read_ref(MAIN_REF)
    if remote_head is None:
        return None
    remote.fetch_metadata(store)  # all commits + trees locally
    blobs = [
        (kind, oid)
        for kind, oid in _reachable_objects(store, remote_head, set())
        if kind == "blobs" and not store.has("blobs", oid)
    ]
    remote.download_objects(store, blobs)  # M7: bulk
    return remote_head


def pull(wit: Path, store: ObjectStore, remote: Remote) -> tuple[str, list[str]] | None:
    """Fetch the remote; fast-forward or, upon divergence, reconcile to a merge.

    Returns (head, conflict-paths), or None if the remote is empty.
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
        return local, []  # local is already ahead; nothing to do
    # divergent -> reconcile into a merge commit (no history loss)
    return reconcile(wit, store, local, head)


def clone(remote: Remote, dest: Path) -> Path:
    wit = init(dest)
    store = ObjectStore(wit)
    head = fetch(store, remote)
    if head is not None:
        update_ref(wit, head_ref(wit), head)
        checkout(wit, store, head)
    return wit
