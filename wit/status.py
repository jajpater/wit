"""`status`: vergelijk de werkdirectory met de index (en met HEAD).

Verander-detectie volgt git's snelle pad: matcht ``(size, mtime, device, inode)`` met de
index-entry, dan nemen we de inhoud ongewijzigd aan; anders herhashen we om een echte
wijziging van een loutere aanraking te onderscheiden. Is er een HEAD-tree meegegeven, dan
geldt een ongewijzigd, gevolgd bestand als *schoon* wanneer zijn hash gelijk is aan HEAD,
en anders als *staged*; zonder HEAD (nog geen commits) is alles in de index 'staged'.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .ignore import load_ignore
from .index import Index, IndexEntry
from .objects import hash_file
from .worktree import rel_path, walk_files


@dataclass
class Status:
    staged: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (
            self.modified or self.deleted or self.untracked or self.conflicts
        )


def _stat_matches(entry: IndexEntry, st) -> bool:
    return (
        entry.size == st.st_size
        and entry.mtime_ns == st.st_mtime_ns
        and entry.device == st.st_dev
        and entry.inode == st.st_ino
    )


def compute_status(
    index: Index, root: Path, head_tree: dict[str, str] | None = None
) -> Status:
    root = Path(root)
    entries = {e.path: e for e in index.entries()}
    ignore = load_ignore(root)
    seen: set[str] = set()
    status = Status()

    for path in walk_files(root):
        rel = rel_path(path, root)
        seen.add(rel)
        entry = entries.get(rel)
        if entry is None:
            # ignore geldt alleen voor niet-gevolgde bestanden (git-semantiek)
            if not ignore.match(rel, False):
                status.untracked.append(rel)
            continue
        unchanged = _stat_matches(entry, path.stat()) or hash_file(path) == entry.hash
        if not unchanged:
            status.modified.append(rel)
        elif head_tree is not None and head_tree.get(rel) == entry.hash:
            pass  # gevolgd, ongewijzigd én gelijk aan HEAD -> schoon
        else:
            status.staged.append(rel)

    for rel in entries:
        if rel not in seen:
            status.deleted.append(rel)

    status.conflicts = index.conflicts()

    for group in (status.staged, status.modified, status.deleted, status.untracked):
        group.sort()
    return status
