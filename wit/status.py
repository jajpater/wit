"""`status`: vergelijk de werkdirectory met de index.

In M1 is er nog geen commit-historie (dat is M2), dus alles in de index geldt als
'toegevoegd/staged'. Verander-detectie volgt git's snelle pad: matcht ``(size, mtime,
device, inode)`` met de index-entry, dan nemen we de inhoud ongewijzigd aan; anders
herhashen we om een echte wijziging van een loutere aanraking te onderscheiden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .index import Index, IndexEntry
from .objects import hash_file
from .worktree import rel_path, walk_files


@dataclass
class Status:
    staged: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.modified or self.deleted or self.untracked)


def _stat_matches(entry: IndexEntry, st) -> bool:
    return (
        entry.size == st.st_size
        and entry.mtime_ns == st.st_mtime_ns
        and entry.device == st.st_dev
        and entry.inode == st.st_ino
    )


def compute_status(index: Index, root: Path) -> Status:
    root = Path(root)
    entries = {e.path: e for e in index.entries()}
    seen: set[str] = set()
    status = Status()

    for path in walk_files(root):
        rel = rel_path(path, root)
        seen.add(rel)
        entry = entries.get(rel)
        if entry is None:
            status.untracked.append(rel)
        elif _stat_matches(entry, path.stat()):
            status.staged.append(rel)
        elif hash_file(path) == entry.hash:
            status.staged.append(rel)  # alleen stat veranderde, inhoud gelijk
        else:
            status.modified.append(rel)

    for rel in entries:
        if rel not in seen:
            status.deleted.append(rel)

    for group in (status.staged, status.modified, status.deleted, status.untracked):
        group.sort()
    return status
