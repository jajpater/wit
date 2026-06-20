"""M1-criterium: untracked/modified (en deleted/staged) correct op een echte map."""

import os

import pytest

from wit.index import Index, IndexEntry
from wit.objects import ObjectStore
from wit.repo import init
from wit.status import compute_status


@pytest.fixture
def repo(tmp_path):
    wit = init(tmp_path)
    return tmp_path, wit


def _add(wit, root, rel):
    """Bootst `wit add` na: blob opslaan + index-entry schrijven."""
    path = root / rel
    store = ObjectStore(wit)
    oid = store.put_file(path, kind="blobs")
    st = path.stat()
    with Index(wit) as index:
        index.put_entry(IndexEntry(
            path=rel, hash=oid, mode=st.st_mode, size=st.st_size,
            mtime_ns=st.st_mtime_ns, ctime_ns=st.st_ctime_ns,
            device=st.st_dev, inode=st.st_ino,
        ))


def test_untracked(repo):
    root, wit = repo
    (root / "boek.pdf").write_bytes(b"%PDF nep")
    with Index(wit) as index:
        status = compute_status(index, root)
    assert status.untracked == ["boek.pdf"]
    assert status.staged == status.modified == status.deleted == []


def test_staged_after_add(repo):
    root, wit = repo
    (root / "boek.pdf").write_bytes(b"%PDF nep")
    _add(wit, root, "boek.pdf")
    with Index(wit) as index:
        status = compute_status(index, root)
    assert status.staged == ["boek.pdf"]
    assert status.untracked == [] and status.modified == []


def test_modified_when_content_changes(repo):
    root, wit = repo
    f = root / "boek.pdf"
    f.write_bytes(b"versie 1")
    _add(wit, root, "boek.pdf")
    f.write_bytes(b"versie 2 langer en anders")  # andere inhoud én grootte
    with Index(wit) as index:
        status = compute_status(index, root)
    assert status.modified == ["boek.pdf"]
    assert status.staged == []


def test_touch_without_content_change_is_not_modified(repo):
    root, wit = repo
    f = root / "boek.pdf"
    f.write_bytes(b"zelfde inhoud")
    _add(wit, root, "boek.pdf")
    # alleen mtime veranderen, inhoud gelijk -> stat wijkt af, hash niet -> staged
    future = (os.stat(f).st_atime_ns + 10**9, os.stat(f).st_mtime_ns + 10**9)
    os.utime(f, ns=future)
    with Index(wit) as index:
        status = compute_status(index, root)
    assert status.modified == []
    assert status.staged == ["boek.pdf"]


def test_deleted_when_file_removed(repo):
    root, wit = repo
    (root / "boek.pdf").write_bytes(b"inhoud")
    _add(wit, root, "boek.pdf")
    (root / "boek.pdf").unlink()
    with Index(wit) as index:
        status = compute_status(index, root)
    assert status.deleted == ["boek.pdf"]


def test_nested_and_skips_wit_dir(repo):
    root, wit = repo
    (root / "sub").mkdir()
    (root / "sub" / "a.txt").write_text("a")
    with Index(wit) as index:
        status = compute_status(index, root)
    # genest pad gevonden, niets uit .wit/ lekt in de status
    assert status.untracked == ["sub/a.txt"]


def test_index_is_rebuildable_cache(repo):
    # Het wissen van de index mag de repo niet schaden; status valt terug op untracked.
    root, wit = repo
    (root / "boek.pdf").write_bytes(b"inhoud")
    _add(wit, root, "boek.pdf")
    (wit / "index.sqlite").unlink()
    with Index(wit) as index:
        status = compute_status(index, root)
    assert status.untracked == ["boek.pdf"]
