"""M0-criteria: put/get werkt, hash klopt, corruptie wordt gedetecteerd,
een partial write laat geen half object achter."""

import os

import pytest
from blake3 import blake3

import wit.objects as objects
from wit.fsck import fsck
from wit.objects import ObjectStore, hash_bytes
from wit.repo import init


@pytest.fixture
def store(tmp_path):
    return ObjectStore(init(tmp_path))


def test_put_get_roundtrip(store):
    data = b"hallo wereld\n"
    oid = store.put("blobs", data)
    assert store.has("blobs", oid)
    assert store.get("blobs", oid) == data


def test_blob_id_is_blake3_of_raw_bytes(store):
    # Raw blobs: de object-id is exact b3sum van het losse bestand → extern verifieerbaar.
    data = b"\x00\x01\x02 ruwe bytes"
    oid = store.put("blobs", data)
    assert oid == "b3:" + blake3(data).hexdigest()


def test_put_is_deduplicated(store):
    data = b"zelfde inhoud"
    assert store.put("blobs", data) == store.put("blobs", data)
    assert sum(1 for _ in store.iter_objects("blobs")) == 1


def test_put_file_streams_and_matches_put_bytes(store, tmp_path):
    data = os.urandom(5 * 1024 * 1024 + 7)  # groter dan de chunkgrootte
    src = tmp_path / "groot.bin"
    src.write_bytes(data)
    oid = store.put_file(src)
    assert oid == hash_bytes(data)
    assert store.get("blobs", oid) == data


def test_corruption_is_detected(store):
    oid = store.put("blobs", b"origineel")
    store._path("blobs", oid).write_bytes(b"aangepast")  # inhoud != hash
    report = fsck(store)
    assert not report.ok
    assert oid in report.corrupt


def test_partial_write_leaves_no_half_object(store, monkeypatch):
    # rename is het commit-punt; faalt die, dan mag er geen object op de
    # definitieve plek staan en geen verweesde tmp achterblijven.
    def boom(*_a, **_k):
        raise RuntimeError("crash tijdens rename")

    monkeypatch.setattr(objects.os, "rename", boom)
    with pytest.raises(RuntimeError):
        store.put("blobs", b"zou half kunnen schrijven")
    assert sum(1 for _ in store.iter_objects("blobs")) == 0
    assert list(store.tmp_dir.iterdir()) == []
