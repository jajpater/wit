"""Fase 4: retentie 'bewaar laatste N' via shallow-grens + GC."""

from wit import porcelain
from wit.commits import log
from wit.objects import ObjectStore
from wit.refs import read_head
from wit.repo import init, read_shallow


def test_retain_keeps_last_n_versions(tmp_path):
    wit = init(tmp_path)
    store = ObjectStore(wit)
    blob_oids = []
    for i in range(1, 6):  # 5 commits, doc.txt v1..v5
        (tmp_path / "doc.txt").write_bytes(f"versie {i}".encode())
        porcelain.add(wit, store, [str(tmp_path / "doc.txt")])
        porcelain.commit(wit, store, f"v{i}", time=f"2026-01-0{i}T00:00:00.000000Z")
        blob_oids.append(store.put("blobs", f"versie {i}".encode()))
    head_before = read_head(wit)

    report = porcelain.retain(wit, store, 2, grace_seconds=0)

    # de drie oudste versies zijn weg, de laatste twee blijven
    assert not store.has("blobs", blob_oids[0])
    assert not store.has("blobs", blob_oids[1])
    assert not store.has("blobs", blob_oids[2])
    assert store.has("blobs", blob_oids[3])
    assert store.has("blobs", blob_oids[4])
    assert report.removed > 0

    # HEAD en werkdir blijven intact
    assert read_head(wit) == head_before
    assert (tmp_path / "doc.txt").read_bytes() == b"versie 5"

    # log stopt bij de shallow-grens: nog 2 commits zichtbaar
    visible = log(store, read_head(wit), read_shallow(wit))
    assert len(visible) == 2


def test_retain_shorter_history_is_noop(tmp_path):
    wit = init(tmp_path)
    store = ObjectStore(wit)
    (tmp_path / "a.txt").write_bytes(b"a")
    porcelain.add(wit, store, [str(tmp_path / "a.txt")])
    porcelain.commit(wit, store, "enige", time="2026-01-01T00:00:00.000000Z")

    report = porcelain.retain(wit, store, 5, grace_seconds=0)  # meer dan er zijn
    assert report.removed == 0
    assert read_shallow(wit) == set()  # geen grens gezet
