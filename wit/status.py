"""`status`: compare the working directory with the index (and with HEAD).

Change detection follows git's fast path: if ``(size, mtime, device, inode)`` matches the
index entry, we assume the content is unchanged; otherwise we re-hash to distinguish a real
change from a mere touch. If a HEAD tree is provided, an
unchanged, tracked file is considered *clean* if its hash matches HEAD,
and otherwise *staged*; without HEAD (no commits yet), everything in the index is 'staged'.
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
    staged_deleted: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        # 'clean' = working directory equals the index; staged additions/deletions are
        # a separate axis (they wait for commit) and intentionally don't count here.
        return not (
            self.modified or self.deleted or self.untracked or self.conflicts
        )

    @property
    def has_staged(self) -> bool:
        return bool(self.staged or self.staged_deleted)


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
            # ignore only applies to untracked files (git semantics)
            if not ignore.match(rel, False):
                status.untracked.append(rel)
            continue
        unchanged = _stat_matches(entry, path.stat()) or hash_file(path) == entry.hash
        if not unchanged:
            status.modified.append(rel)
        elif head_tree is not None and head_tree.get(rel) == entry.hash:
            pass  # tracked, unchanged and equal to HEAD -> clean
        else:
            status.staged.append(rel)

    for rel in entries:
        if rel not in seen:
            status.deleted.append(rel)

    # Staged deletion: was in HEAD, but no longer in the index (e.g. after `wit rm`).
    # The next commit (tree from the index) naturally omits the path.
    if head_tree is not None:
        for rel in head_tree:
            if rel not in entries:
                status.staged_deleted.append(rel)

    status.conflicts = index.conflicts()

    for group in (
        status.staged, status.staged_deleted,
        status.modified, status.deleted, status.untracked,
    ):
        group.sort()
    return status
