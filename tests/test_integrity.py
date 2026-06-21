"""Fase 5: hash-verificatie bij download — corruptie-in-transit blokkeren."""

import pytest

from wit import porcelain, sync
from wit.objects import ObjectStore
from wit.remote import FilesystemRemote
from wit.repo import init

_T = "2026-01-01T00:00:00.000000Z"


def test_ingest_rejects_corrupt_object(tmp_path):
    wit = init(tmp_path / "repo")
    store = ObjectStore(wit)
    oid = store.put("blobs", b"echte inhoud")

    bogus = tmp_path / "bogus"
    bogus.write_bytes(b"andere inhoud")  # hasht niet naar oid
    fake = "b3:" + "0" * 64

    with pytest.raises(ValueError, match="hash-mismatch"):
        store.ingest("blobs", fake, bogus)
    assert not store.has("blobs", fake)  # corrupt object kwam nooit binnen
    assert store.has("blobs", oid)  # bestaande store ongemoeid


def test_download_of_tampered_remote_object_fails(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    wit = init(src)
    store = ObjectStore(wit)
    (src / "doc.txt").write_bytes(b"belangrijk document")
    porcelain.add(wit, store, [str(src)])
    porcelain.commit(wit, store, "init", time=_T)

    remote_dir = tmp_path / "remote"
    sync.push(wit, store, FilesystemRemote(remote_dir))

    # manipuleer een blob op de remote (zelfde id-pad, andere bytes)
    blob_oid = store.put("blobs", b"belangrijk document")
    h = blob_oid.split(":", 1)[1]
    remote_blob = remote_dir / "objects" / "blobs" / h[:2] / h[2:]
    remote_blob.write_bytes(b"GESABOTEERD")

    dest = tmp_path / "clone"
    with pytest.raises(ValueError, match="hash-mismatch"):
        sync.clone(FilesystemRemote(remote_dir), dest)
    # de corrupte blob is niet in de lokale store beland
    assert not ObjectStore(dest / ".wit").has("blobs", blob_oid)
