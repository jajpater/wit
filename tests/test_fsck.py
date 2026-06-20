"""fsck: telt gecontroleerde objecten en ruimt verweesde tmp-bestanden op."""

import pytest

from wit.fsck import fsck
from wit.objects import ObjectStore
from wit.repo import init


@pytest.fixture
def store(tmp_path):
    return ObjectStore(init(tmp_path))


def test_fsck_ok_on_clean_store(store):
    store.put("blobs", b"a")
    store.put("trees", b'{"x": 1}')
    report = fsck(store)
    assert report.ok
    assert report.checked == 2


def test_fsck_cleans_stray_tmp(store):
    (store.tmp_dir / "verweesd").write_bytes(b"afgebroken schrijf")
    report = fsck(store)
    assert report.stray_tmp == 1
    assert list(store.tmp_dir.iterdir()) == []
