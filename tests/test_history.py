"""M2-criteria: stabiele tree-/commit-id's en een correct lopende commit-DAG."""

import pytest

from wit.commits import create_commit, log, read_commit
from wit.index import IndexEntry
from wit.objects import ObjectStore
from wit.refs import head_ref, read_head, update_ref
from wit.repo import init
from wit.trees import build_tree, read_tree


@pytest.fixture
def store(tmp_path):
    return ObjectStore(init(tmp_path))


def _entry(path, data, store):
    oid = store.put("blobs", data)
    return IndexEntry(path=path, hash=oid, mode=0o100644, size=len(data),
                      mtime_ns=0, ctime_ns=0, device=0, inode=0)


def test_tree_id_is_deterministic(store):
    entries = [_entry("a.txt", b"a", store), _entry("dir/b.txt", b"b", store)]
    first = build_tree(entries, store)
    second = build_tree(list(reversed(entries)), store)  # volgorde mag niet uitmaken
    assert first == second
    assert first.startswith("b3:")


def test_tree_nesting_roundtrip(store):
    entries = [_entry("dir/b.txt", b"b", store)]
    root = read_tree(store, build_tree(entries, store))
    assert root["dir"]["type"] == "tree"
    sub = read_tree(store, root["dir"]["hash"])
    assert sub["b.txt"]["type"] == "blob"
    assert sub["b.txt"]["hash"] == store.put("blobs", b"b")


def test_commit_id_stable_and_sensitive(store):
    tree = build_tree([_entry("a.txt", b"a", store)], store)
    kw = dict(time="2026-06-20T12:00:00Z", host="testhost")
    c1 = create_commit(store, tree, [], "eerste", **kw)
    c2 = create_commit(store, tree, [], "eerste", **kw)
    c3 = create_commit(store, tree, [], "andere boodschap", **kw)
    assert c1 == c2          # zelfde inhoud -> zelfde id
    assert c1 != c3          # andere boodschap -> ander id
    assert read_commit(store, c1)["message"] == "eerste"


def test_commit_updates_head(store):
    tree = build_tree([_entry("a.txt", b"a", store)], store)
    cid = create_commit(store, tree, [], "eerste", time="2026-01-01T00:00:00Z")
    update_ref(store.wit_dir, head_ref(store.wit_dir), cid)
    assert read_head(store.wit_dir) == cid


def test_log_linear_newest_first(store):
    tree = build_tree([_entry("a.txt", b"a", store)], store)
    c0 = create_commit(store, tree, [], "nul", time="2026-01-01T00:00:00Z")
    c1 = create_commit(store, tree, [c0], "een", time="2026-01-02T00:00:00Z")
    ids = [cid for cid, _ in log(store, c1)]
    assert ids == [c1, c0]


def test_log_dag_visits_each_commit_once(store):
    # Diamant: c0 <- c1, c0 <- c2, merge m(parents=[c1, c2]).
    tree = build_tree([_entry("a.txt", b"a", store)], store)
    c0 = create_commit(store, tree, [], "basis", time="2026-01-01T00:00:00Z")
    c1 = create_commit(store, tree, [c0], "lijn 1", time="2026-01-02T00:00:00Z")
    c2 = create_commit(store, tree, [c0], "lijn 2", time="2026-01-03T00:00:00Z")
    m = create_commit(store, tree, [c1, c2], "merge", time="2026-01-04T00:00:00Z")
    history = log(store, m)
    ids = [cid for cid, _ in history]
    assert ids == [m, c2, c1, c0]            # gesorteerd op tijd, nieuwste eerst
    assert len(ids) == len(set(ids)) == 4    # gedeelde voorouder c0 precies één keer
    assert len(read_commit(store, m)["parents"]) == 2
