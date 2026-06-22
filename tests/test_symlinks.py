"""Symlinks are skipped by the worktree walk, so ``add`` never crashes on them.

The motivating case: an Emacs lock file ``.#foo.org`` is a *dangling* symlink (its
target does not exist); opening it raised FileNotFoundError and aborted ``wit add .``.
"""

from wit import porcelain
from wit.objects import ObjectStore
from wit.repo import init
from wit.worktree import walk_files


def _repo(path):
    wit = init(path)
    return wit, ObjectStore(wit)


def test_walk_skips_dangling_symlink(tmp_path):
    (tmp_path / "real.txt").write_bytes(b"hi\n")
    (tmp_path / ".#real.txt").symlink_to("user@host.12345:67890")  # dangling
    wit, _ = _repo(tmp_path)

    found = {p.name for p in walk_files(tmp_path, root=tmp_path.parent)}
    assert "real.txt" in found
    assert ".#real.txt" not in found


def test_walk_skips_symlinked_dir(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.txt").write_bytes(b"a\n")
    (tmp_path / "link").symlink_to(tmp_path / "docs")
    wit, _ = _repo(tmp_path)

    rels = {p.relative_to(tmp_path).as_posix()
            for p in walk_files(tmp_path, root=tmp_path.parent)}
    assert "docs/a.txt" in rels
    assert not any(r.startswith("link/") for r in rels)


def test_add_does_not_crash_on_dangling_symlink(tmp_path):
    (tmp_path / "real.txt").write_bytes(b"hi\n")
    (tmp_path / ".#real.txt").symlink_to("nowhere:0")  # dangling editor lock
    wit, store = _repo(tmp_path)

    added = porcelain.add(wit, store, [str(tmp_path)])
    assert added == 1  # only real.txt; the symlink is skipped, no crash
