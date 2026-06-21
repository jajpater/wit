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


def retain(
    wit: Path,
    store: ObjectStore,
    keep_n: int,
    *,
    grace_seconds: float = DEFAULT_GRACE_SECONDS,
) -> GcReport:
    """Bewaar per branch de laatste ``keep_n`` commits; ruim de rest op.

    Zet een shallow-grens op de ``keep_n``-de commit (zijn parents gelden daarna als
    afwezig) en draait dan GC, zodat objecten die uitsluitend bij oudere commits horen
    worden geveegd. Dit is een *lokale* opruiming; een remote met volledige historie
    blijft volledig.
    """
    if keep_n < 1:
        raise ValueError("keep_n moet >= 1 zijn")
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
    """Materialiseer de tree van ``commit_id`` als echte bestanden in de werkdir.

    Volledige kopie (geen symlinks); modebits worden hersteld. Respecteert de sparse-cone
    (`.wit/sparse`): alleen paden in de cone worden uitgecheckt, en eerder uitgecheckte
    bestanden die nu buiten de cone vallen worden verwijderd. Na afloop wordt de index
    herbouwd zodat ``status`` schoon is.
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

    # cone versmald -> eerder uitgecheckte, nu uitgesloten bestanden opruimen
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
