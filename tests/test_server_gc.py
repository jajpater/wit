"""Fase 7: smart-server GC — de remote ruimt zelf veilig op (onder de ref-flock)."""

from wit import porcelain, sync
from wit.objects import ObjectStore
from wit.remote import WitServerRemote
from wit.repo import init

_T = "2026-01-01T00:00:00.000000Z"


def _pushed_server(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    wit = init(src)
    store = ObjectStore(wit)
    (src / "doc.txt").write_bytes(b"inhoud")
    porcelain.add(wit, store, [str(src)])
    head = porcelain.commit(wit, store, "c1", time=_T)
    remote = WitServerRemote(tmp_path / "remote")
    sync.push(wit, store, remote)
    return remote, head


def test_server_gc_sweeps_unreachable_remote_object(tmp_path):
    remote, _ = _pushed_server(tmp_path)
    orphan = remote.store.put("blobs", b"nergens naar verwezen op de remote")
    assert remote.store.has("blobs", orphan)

    report = remote.gc(grace_seconds=0)
    assert report.removed == 1
    assert not remote.store.has("blobs", orphan)


def test_server_gc_keeps_reachable_and_cas_still_works(tmp_path):
    remote, head = _pushed_server(tmp_path)
    before = {
        (k, o) for k in ("blobs", "trees", "commits") for o in remote.store.iter_objects(k)
    }

    report = remote.gc(grace_seconds=0)
    assert report.removed == 0
    after = {
        (k, o) for k in ("blobs", "trees", "commits") for o in remote.store.iter_objects(k)
    }
    assert before == after

    # de ref-CAS blijft werken na een GC (zelfde lockbestand, netjes vrijgegeven)
    new = "b3:" + "1" * 64
    assert remote.compare_and_swap_ref("refs/heads/main", head, new)
    assert remote.read_ref("refs/heads/main") == new


def test_server_gc_grace_protects_young_objects(tmp_path):
    remote, _ = _pushed_server(tmp_path)
    orphan = remote.store.put("blobs", b"jong wees-object")

    report = remote.gc(grace_seconds=3600)
    assert report.removed == 0
    assert report.skipped_young == 1
    assert remote.store.has("blobs", orphan)
