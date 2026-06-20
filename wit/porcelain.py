"""High-level operaties: add, commit, checkout.

Deze laag bindt object store, index, trees, commits en refs samen tot de commando's
die de gebruiker kent. De CLI is er een dunne schil omheen; tests gebruiken deze
functies rechtstreeks (los van cwd/argparse).
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from pathlib import Path

from .commits import create_commit, read_commit
from .ignore import load_ignore
from .index import Index, IndexEntry
from .objects import ObjectStore
from .refs import head_ref, read_head, update_ref
from .trees import build_tree, read_tree
from .worktree import rel_path, walk_files


def _entry_for(rel: str, oid: str, st: os.stat_result) -> IndexEntry:
    return IndexEntry(
        path=rel, hash=oid, mode=st.st_mode, size=st.st_size,
        mtime_ns=st.st_mtime_ns, ctime_ns=st.st_ctime_ns,
        device=st.st_dev, inode=st.st_ino,
    )


def add(wit: Path, store: ObjectStore, targets: Iterable[str]) -> int:
    """Neem bestanden onder beheer: blob opslaan + index-entry schrijven.

    Bij het aflopen van een map worden `.witignore`-patronen toegepast; een expliciet
    genoemd bestand wordt altijd toegevoegd (vergelijk ``git add -f``).
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
    """Haal bestanden uit beheer (en verwijder ze, tenzij ``keep_file``).

    Een target mag een bestand of een map zijn; bij een map worden alle gevolgde
    paden eronder verwijderd. De commit erna mist de paden vanzelf (tree uit de index).
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
    """Leg de staged toestand (de index) vast als commit; geef de commit-id terug."""
    with Index(wit) as index:
        entries = index.entries()
    if not entries:
        raise ValueError("niets om te committen (index is leeg)")
    tree = build_tree(entries, store)
    parents = [head] if (head := read_head(wit)) else []
    commit_id = create_commit(store, tree, parents, message, **kw)
    update_ref(wit, head_ref(wit), commit_id)
    return commit_id


def iter_tree(
    store: ObjectStore, tree_oid: str, prefix: str = ""
) -> Iterator[tuple[str, dict]]:
    """Loop een tree recursief af tot platte (pad, blob-entry)-paren."""
    for name, entry in read_tree(store, tree_oid).items():
        rel = f"{prefix}{name}"
        if entry["type"] == "tree":
            yield from iter_tree(store, entry["hash"], rel + "/")
        else:
            yield rel, entry


def tree_map(store: ObjectStore, tree_oid: str) -> dict[str, str]:
    """Platte ``pad -> blob-hash`` van een tree (voor status-vs-HEAD)."""
    return {rel: entry["hash"] for rel, entry in iter_tree(store, tree_oid)}


def checkout(wit: Path, store: ObjectStore, commit_id: str) -> int:
    """Materialiseer de tree van ``commit_id`` als echte bestanden in de werkdir.

    Volledige kopie (geen symlinks); modebits worden hersteld. Na afloop wordt de
    index herbouwd zodat ``status`` schoon is.
    """
    root = wit.parent
    tree = read_commit(store, commit_id)["tree"]
    materialized: list[tuple[str, dict]] = []
    for rel, entry in iter_tree(store, tree):
        target = root / rel
        store.copy_to("blobs", entry["hash"], target)
        os.chmod(target, entry["mode"] & 0o7777)
        materialized.append((rel, entry))

    with Index(wit) as index:
        index.clear()
        for rel, entry in materialized:
            index.put_entry(_entry_for(rel, entry["hash"], (root / rel).stat()))
    return len(materialized)
