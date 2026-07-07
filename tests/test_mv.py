"""wit mv — verplaatsen/hernoemen, plus het stagen van verwijderingen door `add`.

`add` volgt sinds git 2.0: een `add <pad>` staget onder dat pad óók
verwijderingen (getrackte bestanden die van schijf verdwenen zijn). Daardoor
werkt een verplaatsing via de shell (`mv` + `wit add .`) vanzelf, en biedt
`wit mv` dat in één commando aan.
"""

import pytest

from wit import porcelain
from wit.commits import read_commit
from wit.index import Index
from wit.objects import ObjectStore
from wit.porcelain import iter_tree
from wit.refs import read_head
from wit.repo import init


def _setup(tmp_path):
    wit = init(tmp_path)
    store = ObjectStore(wit)
    (tmp_path / "foo").mkdir()
    (tmp_path / "foo" / "a.txt").write_bytes(b"a")
    (tmp_path / "foo" / "b.txt").write_bytes(b"b")
    (tmp_path / "top.txt").write_bytes(b"t")
    porcelain.add(wit, store, [str(tmp_path)])
    porcelain.commit(wit, store, "init", time="2026-01-01T00:00:00.000000Z")
    return tmp_path, wit, store


def _tracked(wit):
    with Index(wit) as index:
        return {e.path for e in index.entries()}


def _committed(store, wit):
    tree = read_commit(store, read_head(wit))["tree"]
    return {p for p, _ in iter_tree(store, tree)}


# -- add staget verwijderingen -------------------------------------------------

def test_add_stages_deletion_of_moved_dir(tmp_path):
    root, wit, store = _setup(tmp_path)
    (root / "foo").rename(root / "bar")  # verplaats via de "shell"
    porcelain.add(wit, store, [str(root)])
    tracked = _tracked(wit)
    assert tracked == {"bar/a.txt", "bar/b.txt", "top.txt"}  # foo/ is weg


def test_add_stages_plain_deletion(tmp_path):
    root, wit, store = _setup(tmp_path)
    (root / "top.txt").unlink()
    porcelain.add(wit, store, [str(root)])
    assert "top.txt" not in _tracked(wit)


def test_add_pathspec_scopes_deletion(tmp_path):
    root, wit, store = _setup(tmp_path)
    (root / "foo" / "a.txt").unlink()
    (root / "top.txt").unlink()
    # alleen onder foo/ stagen -> top.txt blijft getrackt
    porcelain.add(wit, store, [str(root / "foo")])
    tracked = _tracked(wit)
    assert "foo/a.txt" not in tracked
    assert "top.txt" in tracked


# -- wit mv --------------------------------------------------------------------

def test_mv_renames_directory(tmp_path):
    root, wit, store = _setup(tmp_path)
    moved = porcelain.mv(wit, store, [str(root / "foo")], str(root / "bar"))
    assert moved == 2
    assert _tracked(wit) == {"bar/a.txt", "bar/b.txt", "top.txt"}
    assert (root / "bar" / "a.txt").exists()
    assert not (root / "foo").exists()


def test_mv_renames_file(tmp_path):
    root, wit, store = _setup(tmp_path)
    moved = porcelain.mv(wit, store, [str(root / "top.txt")], str(root / "renamed.txt"))
    assert moved == 1
    assert "renamed.txt" in _tracked(wit)
    assert "top.txt" not in _tracked(wit)


def test_mv_into_existing_directory(tmp_path):
    root, wit, store = _setup(tmp_path)
    (root / "dest").mkdir()
    moved = porcelain.mv(
        wit, store, [str(root / "foo" / "a.txt"), str(root / "top.txt")], str(root / "dest")
    )
    assert moved == 2
    tracked = _tracked(wit)
    assert {"dest/a.txt", "dest/top.txt"} <= tracked
    assert "foo/a.txt" not in tracked and "top.txt" not in tracked


def test_mv_reflected_in_commit(tmp_path):
    root, wit, store = _setup(tmp_path)
    porcelain.mv(wit, store, [str(root / "foo")], str(root / "bar"))
    porcelain.commit(wit, store, "mv", time="2026-01-02T00:00:00.000000Z")
    assert _committed(store, wit) == {"bar/a.txt", "bar/b.txt", "top.txt"}


def test_mv_reuses_blob_no_rehash(tmp_path):
    # De blob is inhoudelijk gelijk: mv hergebruikt de hash uit de oude entry.
    root, wit, store = _setup(tmp_path)
    with Index(wit) as index:
        before = {e.path: e.hash for e in index.entries()}
    porcelain.mv(wit, store, [str(root / "top.txt")], str(root / "renamed.txt"))
    with Index(wit) as index:
        after = {e.path: e.hash for e in index.entries()}
    assert after["renamed.txt"] == before["top.txt"]


def test_mv_missing_source_errors(tmp_path):
    root, wit, store = _setup(tmp_path)
    with pytest.raises(ValueError):
        porcelain.mv(wit, store, [str(root / "nope.txt")], str(root / "x.txt"))


def test_mv_existing_destination_errors(tmp_path):
    root, wit, store = _setup(tmp_path)
    with pytest.raises(ValueError):
        porcelain.mv(wit, store, [str(root / "top.txt")], str(root / "foo" / "a.txt"))


def test_mv_multiple_requires_directory(tmp_path):
    root, wit, store = _setup(tmp_path)
    with pytest.raises(ValueError):
        porcelain.mv(
            wit, store,
            [str(root / "foo" / "a.txt"), str(root / "foo" / "b.txt")],
            str(root / "not_a_dir"),
        )
