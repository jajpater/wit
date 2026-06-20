"""Fase 3: gedeeltelijke (sparse) checkout."""

from wit import porcelain
from wit.commits import read_commit
from wit.index import Index
from wit.objects import ObjectStore
from wit.refs import read_head
from wit.repo import init, set_sparse
from wit.status import compute_status


def _setup(tmp_path):
    wit = init(tmp_path)
    store = ObjectStore(wit)
    (tmp_path / "teksten").mkdir()
    (tmp_path / "teksten" / "a.md").write_bytes(b"a")
    (tmp_path / "scans").mkdir()
    (tmp_path / "scans" / "b.tif").write_bytes(b"b")
    (tmp_path / "los.txt").write_bytes(b"los")
    porcelain.add(wit, store, [str(tmp_path)])
    porcelain.commit(wit, store, "init", time="2026-01-01T00:00:00.000000Z")
    return tmp_path, wit, store


def _tracked(wit):
    with Index(wit) as index:
        return {e.path for e in index.entries()}


def test_sparse_checkout_materializes_only_cone(tmp_path):
    root, wit, store = _setup(tmp_path)
    set_sparse(wit, ["teksten/"])
    n = porcelain.checkout(wit, store, read_head(wit))
    assert n == 1
    assert (root / "teksten" / "a.md").exists()
    assert not (root / "scans" / "b.tif").exists()   # buiten de cone, verwijderd
    assert not (root / "los.txt").exists()
    assert _tracked(wit) == {"teksten/a.md"}


def test_status_clean_after_sparse_checkout(tmp_path):
    # buiten de cone gevallen paden mogen niet als 'verwijderd' verschijnen
    root, wit, store = _setup(tmp_path)
    set_sparse(wit, ["teksten/"])
    porcelain.checkout(wit, store, read_head(wit))
    head_tree = porcelain.tree_map(store, read_commit(store, read_head(wit))["tree"])
    with Index(wit) as index:
        status = compute_status(index, root, head_tree)
    assert status.clean and not status.staged


def test_widening_cone_restores_files(tmp_path):
    root, wit, store = _setup(tmp_path)
    set_sparse(wit, ["teksten/"])
    porcelain.checkout(wit, store, read_head(wit))
    assert not (root / "scans" / "b.tif").exists()

    set_sparse(wit, [])  # leeg = alles
    n = porcelain.checkout(wit, store, read_head(wit))
    assert n == 3
    assert (root / "scans" / "b.tif").read_bytes() == b"b"
    assert (root / "los.txt").read_bytes() == b"los"
