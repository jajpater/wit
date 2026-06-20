"""M3-criterium: round-trip byte-identiek (add -> commit -> wissen -> checkout)."""

import os

from wit import porcelain
from wit.commits import read_commit
from wit.index import Index
from wit.objects import ObjectStore
from wit.refs import read_head
from wit.repo import init
from wit.status import compute_status
from wit.worktree import walk_files


def _setup(tmp_path):
    wit = init(tmp_path)
    return tmp_path, wit, ObjectStore(wit)


def test_roundtrip_byte_identical(tmp_path):
    root, wit, store = _setup(tmp_path)
    files = {
        "a.txt": b"gewone tekst\n",
        "sub/img.bin": os.urandom(3 * 1024 * 1024 + 11),   # groot + binair
        "sub/diep/leeg.dat": b"",                           # leeg bestand
        "script.sh": b"#!/bin/sh\necho hoi\n",
    }
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    os.chmod(root / "script.sh", 0o755)

    porcelain.add(wit, store, [str(root)])
    porcelain.commit(wit, store, "alles", time="2026-01-01T00:00:00.000000Z")

    # werkdir wissen
    for path in list(walk_files(root)):
        path.unlink()
    assert list(walk_files(root)) == []

    n = porcelain.checkout(wit, store, read_head(wit))
    assert n == len(files)

    for rel, data in files.items():
        assert (root / rel).read_bytes() == data, rel
    # exec-bit hersteld
    assert os.stat(root / "script.sh").st_mode & 0o111

    # na checkout is status schoon (alles gelijk aan HEAD)
    head_tree = porcelain.tree_map(store, read_commit(store, read_head(wit))["tree"])
    with Index(wit) as index:
        status = compute_status(index, root, head_tree)
    assert status.clean and not status.staged


def test_status_clean_after_commit(tmp_path):
    root, wit, store = _setup(tmp_path)
    (root / "boek.pdf").write_bytes(b"%PDF nep")
    porcelain.add(wit, store, [str(root / "boek.pdf")])
    porcelain.commit(wit, store, "boek", time="2026-01-01T00:00:00.000000Z")

    head_tree = porcelain.tree_map(store, read_commit(store, read_head(wit))["tree"])
    with Index(wit) as index:
        status = compute_status(index, root, head_tree)
    assert status.clean and not status.staged  # niet langer eeuwig 'staged'

    # wijzig na commit -> modified
    (root / "boek.pdf").write_bytes(b"%PDF anders en langer")
    with Index(wit) as index:
        status = compute_status(index, root, head_tree)
    assert status.modified == ["boek.pdf"]


def test_checkout_older_commit(tmp_path):
    root, wit, store = _setup(tmp_path)
    f = root / "doc.txt"
    f.write_bytes(b"versie 1")
    porcelain.add(wit, store, [str(f)])
    c1 = porcelain.commit(wit, store, "v1", time="2026-01-01T00:00:00.000000Z")
    f.write_bytes(b"versie 2")
    porcelain.add(wit, store, [str(f)])
    porcelain.commit(wit, store, "v2", time="2026-01-02T00:00:00.000000Z")

    porcelain.checkout(wit, store, c1)
    assert f.read_bytes() == b"versie 1"
