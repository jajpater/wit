"""Fase 1: wit rm — untracken (+ optioneel verwijderen) van bestanden."""

from wit import porcelain
from wit.commits import read_commit
from wit.index import Index
from wit.objects import ObjectStore
from wit.refs import read_head
from wit.repo import init


def _setup(tmp_path):
    wit = init(tmp_path)
    store = ObjectStore(wit)
    (tmp_path / "a.txt").write_bytes(b"a")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_bytes(b"b")
    porcelain.add(wit, store, [str(tmp_path)])
    porcelain.commit(wit, store, "init", time="2026-01-01T00:00:00.000000Z")
    return tmp_path, wit, store


def _tracked(wit):
    with Index(wit) as index:
        return {e.path for e in index.entries()}


def test_rm_untracks_and_deletes(tmp_path):
    root, wit, store = _setup(tmp_path)
    removed = porcelain.rm(wit, store, [str(root / "a.txt")])
    assert removed == 1
    assert "a.txt" not in _tracked(wit)
    assert not (root / "a.txt").exists()


def test_rm_cached_keeps_file(tmp_path):
    root, wit, store = _setup(tmp_path)
    porcelain.rm(wit, store, [str(root / "a.txt")], keep_file=True)
    assert "a.txt" not in _tracked(wit)
    assert (root / "a.txt").exists()  # bestand blijft


def test_rm_directory_recurses(tmp_path):
    root, wit, store = _setup(tmp_path)
    removed = porcelain.rm(wit, store, [str(root / "sub")])
    assert removed == 1
    assert "sub/b.txt" not in _tracked(wit)
    assert not (root / "sub" / "b.txt").exists()


def test_status_shows_staged_deletion_after_rm(tmp_path):
    from wit.status import compute_status

    root, wit, store = _setup(tmp_path)
    head_tree = porcelain.tree_map(store, read_commit(store, read_head(wit))["tree"])
    porcelain.rm(wit, store, [str(root / "a.txt")])

    with Index(wit) as index:
        st = compute_status(index, root, head_tree)
    # weg uit index én werkmap, maar nog in HEAD -> staged verwijdering, niet 'schoon'
    assert st.staged_deleted == ["a.txt"]
    assert st.deleted == []          # geen unstaged deletie (index en werkmap kloppen)
    assert st.has_staged
    assert st.clean                  # werkmap is wel gelijk aan de index


def test_status_shows_unstaged_deletion(tmp_path):
    from wit.status import compute_status

    root, wit, store = _setup(tmp_path)
    head_tree = porcelain.tree_map(store, read_commit(store, read_head(wit))["tree"])
    (root / "a.txt").unlink()        # alleen uit de werkmap (geen wit rm)

    with Index(wit) as index:
        st = compute_status(index, root, head_tree)
    # nog in index én HEAD, weg uit werkmap -> unstaged deletie
    assert st.deleted == ["a.txt"]
    assert st.staged_deleted == []
    assert not st.clean


def test_commit_after_rm_omits_path(tmp_path):
    root, wit, store = _setup(tmp_path)
    porcelain.rm(wit, store, [str(root / "a.txt")])
    porcelain.commit(wit, store, "weg met a", time="2026-01-02T00:00:00.000000Z")
    from wit.trees import read_tree
    tree = read_commit(store, read_head(wit))["tree"]
    names = read_tree(store, tree)
    assert "a.txt" not in names
    assert "sub" in names
