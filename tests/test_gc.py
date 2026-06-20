"""GC: mark -> grace -> sweep. Onbereikbaar weg, bereikbaar + grace behouden."""

from wit import porcelain
from wit.gc import gc
from wit.objects import ObjectStore
from wit.repo import init


def _setup(tmp_path):
    wit = init(tmp_path)
    store = ObjectStore(wit)
    (tmp_path / "doc.txt").write_bytes(b"inhoud")
    porcelain.add(wit, store, [str(tmp_path / "doc.txt")])
    porcelain.commit(wit, store, "c1", time="2026-01-01T00:00:00.000000Z")
    return tmp_path, wit, store


def test_unreachable_object_is_swept(tmp_path):
    _, wit, store = _setup(tmp_path)
    orphan = store.put("blobs", b"nergens naar verwezen")
    assert store.has("blobs", orphan)

    report = gc(wit, store, grace_seconds=0)
    assert report.removed == 1
    assert not store.has("blobs", orphan)


def test_reachable_objects_are_kept(tmp_path):
    _, wit, store = _setup(tmp_path)
    before = {(k, o) for k in ("blobs", "trees", "commits") for o in store.iter_objects(k)}
    report = gc(wit, store, grace_seconds=0)
    assert report.removed == 0
    after = {(k, o) for k in ("blobs", "trees", "commits") for o in store.iter_objects(k)}
    assert before == after


def test_grace_period_protects_young_objects(tmp_path):
    _, wit, store = _setup(tmp_path)
    orphan = store.put("blobs", b"jong wees-object")

    # ruim grace -> jong object blijft staan
    report = gc(wit, store, grace_seconds=3600)
    assert report.removed == 0
    assert report.skipped_young == 1
    assert store.has("blobs", orphan)

    # grace 0 -> nu wel weg
    assert gc(wit, store, grace_seconds=0).removed == 1


def test_history_is_preserved(tmp_path):
    # oude versie van een bestand blijft bereikbaar via de oude commit
    _, wit, store = _setup(tmp_path)
    old_blob = store.put("blobs", b"inhoud")  # == blob van c1's doc.txt
    (tmp_path / "doc.txt").write_bytes(b"versie 2")
    porcelain.add(wit, store, [str(tmp_path / "doc.txt")])
    porcelain.commit(wit, store, "c2", time="2026-01-02T00:00:00.000000Z")

    gc(wit, store, grace_seconds=0)
    # c1 is voorouder van c2 -> zijn tree en oude blob blijven bereikbaar
    assert store.has("blobs", old_blob)


def test_staged_blob_is_not_swept(tmp_path):
    _, wit, store = _setup(tmp_path)
    # nieuw bestand toevoegen (staged), niet committen
    (tmp_path / "nieuw.txt").write_bytes(b"staged maar niet gecommit")
    porcelain.add(wit, store, [str(tmp_path / "nieuw.txt")])
    staged = store.put("blobs", b"staged maar niet gecommit")

    gc(wit, store, grace_seconds=0)
    assert store.has("blobs", staged)  # index telt als root
