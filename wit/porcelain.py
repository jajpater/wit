"""High-level operations: add, commit, checkout.

This layer binds object store, index, trees, commits, and refs together into the commands
that the user knows. The CLI is a thin shell around it; tests use these
functions directly (independent of cwd/argparse).
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from pathlib import Path

from .i18n import _
from .commits import create_commit, read_commit
from .gc import DEFAULT_GRACE_SECONDS, GcReport, gc
from .ignore import load_ignore
from .index import Index, IndexEntry
from .objects import ObjectStore
from .refs import head_ref, read_head, update_ref
from .repo import (
    head_commits,
    read_shallow,
    read_sparse,
    sparse_includes,
    write_shallow,
)
from .trees import build_tree, read_tree
from .worktree import rel_path, walk_files


def _entry_for(rel: str, oid: str, st: os.stat_result) -> IndexEntry:
    return IndexEntry(
        path=rel, hash=oid, mode=st.st_mode, size=st.st_size,
        mtime_ns=st.st_mtime_ns, ctime_ns=st.st_ctime_ns,
        device=st.st_dev, inode=st.st_ino,
    )


def add(wit: Path, store: ObjectStore, targets: Iterable[str]) -> int:
    """Start tracking files: save blob + write index entry.

    When walking a directory, `.witignore` patterns are applied; an explicitly
    named file is always added (similar to ``git add -f``).
    """
    root = wit.parent
    ignore = load_ignore(root)
    count = 0
    with Index(wit) as index:
        for raw in targets:
            for path in walk_files(Path(raw).resolve(), root=root, ignore=ignore):
                rel = rel_path(path, root)
                oid = store.put_file(path, kind="blobs")
                index.put_entry(_entry_for(rel, oid, path.stat()))
                count += 1
    return count


def rm(
    wit: Path, store: ObjectStore, targets: Iterable[str], *, keep_file: bool = False
) -> int:
    """Stop tracking files (and delete them unless ``keep_file`` is True).

    A target can be a file or a directory; for a directory, all tracked
    paths beneath it are removed. The next commit will omit these paths naturally.
    """
    root = wit.parent
    count = 0
    with Index(wit) as index:
        tracked = {e.path for e in index.entries()}
        for raw in targets:
            rel = rel_path(Path(raw).resolve(), root)
            matched = [p for p in tracked if p == rel or p.startswith(rel + "/")]
            for path in matched:
                index.remove(path)
                count += 1
                if not keep_file:
                    target = root / path
                    if target.exists():
                        target.unlink()
    return count


def commit(wit: Path, store: ObjectStore, message: str, **kw: str) -> str:
    """Record the staged state (the index) as a commit; return the commit-id."""
    with Index(wit) as index:
        entries = index.entries()
    if not entries:
        raise ValueError(_("nothing to commit (index is empty)"))
    tree = build_tree(entries, store)
    parents = [head] if (head := read_head(wit)) else []
    commit_id = create_commit(store, tree, parents, message, **kw)
    update_ref(wit, head_ref(wit), commit_id)
    return commit_id


def iter_tree(
    store: ObjectStore, tree_oid: str, prefix: str = ""
) -> Iterator[tuple[str, dict]]:
    """Recursively walk a tree yielding flat (path, blob-entry) tuples."""
    for name, entry in read_tree(store, tree_oid).items():
        rel = f"{prefix}{name}"
        if entry["type"] == "tree":
            yield from iter_tree(store, entry["hash"], rel + "/")
        else:
            yield rel, entry


def tree_map(store: ObjectStore, tree_oid: str) -> dict[str, str]:
    """Flat ``path -> blob-hash`` map of a tree (for status-vs-HEAD)."""
    return {rel: entry["hash"] for rel, entry in iter_tree(store, tree_oid)}


def retain(
    wit: Path,
    store: ObjectStore,
    keep_n: int,
    *,
    grace_seconds: float = DEFAULT_GRACE_SECONDS,
) -> GcReport:
    """Retain the last ``keep_n`` commits per branch; clean up the rest.

    Sets a shallow boundary at the ``keep_n``-th commit (its parents are considered
    absent) and then runs GC, so objects belonging exclusively to older commits
    are swept. This is a *local* cleanup; a remote with full history
    remains complete.
    """
    if keep_n < 1:
        raise ValueError(_("keep_n must be >= 1"))
    boundaries: set[str] = set()
    for head in head_commits(wit):
        cid: str | None = head
        for _ in range(keep_n - 1):
            parents = read_commit(store, cid)["parents"]
            if not parents:
                cid = None
                break
            cid = parents[0]
        if cid is not None and read_commit(store, cid)["parents"]:
            boundaries.add(cid)
    if boundaries:
        write_shallow(wit, read_shallow(wit) | boundaries)
    return gc(wit, store, grace_seconds=grace_seconds)


def checkout(wit: Path, store: ObjectStore, commit_id: str) -> int:
    """Materialize the tree of ``commit_id`` as real files in the working directory.

    Full copy (no symlinks); modebits are restored. Respects the sparse cone
    (`.wit/sparse`): only paths in the cone are checked out, and previously checked out
    files that now fall outside the cone are removed. Afterwards, the index is
    rebuilt so ``status`` is clean.
    """
    root = wit.parent
    sparse = read_sparse(wit)
    with Index(wit) as index:
        old_paths = {e.path for e in index.entries()}

    tree = read_commit(store, commit_id)["tree"]
    materialized: list[tuple[str, dict]] = []
    for rel, entry in iter_tree(store, tree):
        if not sparse_includes(sparse, rel):
            continue
        target = root / rel
        store.copy_to("blobs", entry["hash"], target)
        os.chmod(target, entry["mode"] & 0o7777)
        materialized.append((rel, entry))

    # cone narrowed -> clean up previously checked out, now excluded files
    new_paths = {rel for rel, _ in materialized}
    for path in old_paths - new_paths:
        target = root / path
        if target.exists():
            target.unlink()

    with Index(wit) as index:
        index.clear()
        for rel, entry in materialized:
            index.put_entry(_entry_for(rel, entry["hash"], (root / rel).stat()))
    return len(materialized)
