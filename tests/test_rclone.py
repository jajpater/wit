"""M5b: DumbRcloneRemote — mirror/backup en clone via rclone (lokaal backend)."""

import os

import pytest

from wit import porcelain, sync
from wit.objects import ObjectStore
from wit.rclone import DumbRcloneRemote, have_rclone
from wit.refs import read_head
from wit.repo import init

pytestmark = pytest.mark.skipif(not have_rclone(), reason="rclone niet geïnstalleerd")

_T = "2026-01-01T00:00:00.000000Z"


def _commit(root, files):
    wit = init(root)
    store = ObjectStore(wit)
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    porcelain.add(wit, store, [str(root)])
    porcelain.commit(wit, store, "init", time=_T)
    return wit, store


def test_push_and_clone_via_rclone_byte_identical(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    files = {"a.txt": b"hallo\n", "sub/img.bin": os.urandom(1024 * 1024 + 3)}
    wit, store = _commit(src, files)

    # rclone met een lokaal pad als 'backend' — oefent de echte rclone-codepad
    remote = DumbRcloneRemote(str(tmp_path / "remote"))
    sync.push(wit, store, remote)

    dest = tmp_path / "clone"
    cloned = sync.clone(DumbRcloneRemote(str(tmp_path / "remote")), dest)

    assert read_head(cloned) == read_head(wit)
    for rel, data in files.items():
        assert (dest / rel).read_bytes() == data, rel


def test_rclone_push_uses_constant_calls(tmp_path):
    # M7: het aantal rclone-aanroepen mag niet meegroeien met het aantal bestanden.
    src = tmp_path / "src"
    src.mkdir()
    files = {f"f{i:03d}.txt": f"inhoud {i}".encode() for i in range(40)}
    wit, store = _commit(src, files)

    remote = DumbRcloneRemote(str(tmp_path / "remote"))
    calls = {"n": 0}
    inner = remote._run

    def counting(args, **kw):
        calls["n"] += 1
        return inner(args, **kw)

    remote._run = counting  # type: ignore[method-assign]
    sync.push(wit, store, remote)

    # ~ read_ref(1) + bulk copy per kind(blobs/trees/commits = 3) + CAS(cat+rcat = 2)
    assert calls["n"] <= 10, f"te veel rclone-calls voor 40 bestanden: {calls['n']}"


def test_rclone_ref_cas_is_best_effort(tmp_path):
    remote = DumbRcloneRemote(str(tmp_path / "remote"))
    assert remote.read_ref("refs/heads/main") is None
    assert remote.compare_and_swap_ref("refs/heads/main", None, "b3:aaa")
    # verkeerde verwachting -> geweigerd (clobber-detectie)
    assert not remote.compare_and_swap_ref("refs/heads/main", None, "b3:bbb")
    assert remote.read_ref("refs/heads/main") == "b3:aaa"
    # juiste verwachting -> geslaagd
    assert remote.compare_and_swap_ref("refs/heads/main", "b3:aaa", "b3:ccc")
    assert remote.read_ref("refs/heads/main") == "b3:ccc"
