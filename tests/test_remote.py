"""M5a-criterium: clone/pull vanaf lege map byte-identiek (FilesystemRemote)."""

import os

import pytest

from wit import porcelain, sync
from wit.objects import ObjectStore
from wit.refs import read_head
from wit.remote import FilesystemRemote
from wit.repo import init

_T = "2026-01-01T00:00:00.000000Z"


def _new_repo(path):
    wit = init(path)
    return wit, ObjectStore(wit)


def _write(root, rel, data):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_push_then_clone_is_byte_identical(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    wit, store = _new_repo(src)
    files = {
        "a.txt": b"hallo\n",
        "sub/img.bin": os.urandom(2 * 1024 * 1024 + 5),
        "sub/diep/leeg.dat": b"",
    }
    for rel, data in files.items():
        _write(src, rel, data)
    porcelain.add(wit, store, [str(src)])
    porcelain.commit(wit, store, "init", time=_T)

    remote = FilesystemRemote(tmp_path / "remote")
    sync.push(wit, store, remote)

    dest = tmp_path / "clone"
    cloned = sync.clone(FilesystemRemote(tmp_path / "remote"), dest)

    assert read_head(cloned) == read_head(wit)
    for rel, data in files.items():
        assert (dest / rel).read_bytes() == data, rel


def test_pull_fast_forward(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    wit, store = _new_repo(src)
    _write(src, "a.txt", b"v1")
    porcelain.add(wit, store, [str(src)])
    porcelain.commit(wit, store, "c1", time=_T)
    remote = FilesystemRemote(tmp_path / "remote")
    sync.push(wit, store, remote)

    clone_dir = tmp_path / "clone"
    cwit = sync.clone(FilesystemRemote(tmp_path / "remote"), clone_dir)

    # bron voegt een commit toe en pusht
    _write(src, "b.txt", b"nieuw bestand")
    porcelain.add(wit, store, [str(src)])
    head2 = porcelain.commit(wit, store, "c2", time="2026-01-02T00:00:00.000000Z")
    sync.push(wit, store, remote)

    # clone pullt -> krijgt de nieuwe commit en het nieuwe bestand
    pulled, conflicts = sync.pull(
        cwit, ObjectStore(cwit), FilesystemRemote(tmp_path / "remote")
    )
    assert pulled == head2
    assert conflicts == []
    assert read_head(cwit) == head2
    assert (clone_dir / "b.txt").read_bytes() == b"nieuw bestand"


def test_push_non_fastforward_is_rejected(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    wit, store = _new_repo(src)
    _write(src, "a.txt", b"basis")
    porcelain.add(wit, store, [str(src)])
    porcelain.commit(wit, store, "basis", time=_T)
    remote = FilesystemRemote(tmp_path / "remote")
    sync.push(wit, store, remote)

    # twee klonen vanaf dezelfde basis
    a = sync.clone(FilesystemRemote(tmp_path / "remote"), tmp_path / "a")
    b = sync.clone(FilesystemRemote(tmp_path / "remote"), tmp_path / "b")

    _write(tmp_path / "a", "a.txt", b"door a gewijzigd")
    porcelain.add(a, ObjectStore(a), [str(tmp_path / "a")])
    porcelain.commit(a, ObjectStore(a), "a-werk", time="2026-01-02T00:00:00.000000Z")
    sync.push(a, ObjectStore(a), FilesystemRemote(tmp_path / "remote"))

    _write(tmp_path / "b", "a.txt", b"door b gewijzigd")
    porcelain.add(b, ObjectStore(b), [str(tmp_path / "b")])
    porcelain.commit(b, ObjectStore(b), "b-werk", time="2026-01-03T00:00:00.000000Z")
    with pytest.raises(ValueError, match="non-fast-forward"):
        sync.push(b, ObjectStore(b), FilesystemRemote(tmp_path / "remote"))


def test_clone_only_transfers_missing_objects(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    wit, store = _new_repo(src)
    _write(src, "a.txt", b"x")
    porcelain.add(wit, store, [str(src)])
    porcelain.commit(wit, store, "c1", time=_T)
    remote = FilesystemRemote(tmp_path / "remote")
    sync.push(wit, store, remote)

    # tweede push zonder wijziging: niets te doen, ref blijft staan
    head = sync.push(wit, store, remote)
    assert head == read_head(wit)
    assert remote.read_ref("refs/heads/main") == head
