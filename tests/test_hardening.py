"""M4-criteria: streaming (geen geheugenpiek t.o.v. bestandsgrootte) + .witignore."""

import os
import tracemalloc

from wit import porcelain
from wit.fsck import fsck
from wit.ignore import IgnoreRules
from wit.index import Index
from wit.objects import ObjectStore
from wit.repo import init
from wit.status import compute_status


def _setup(tmp_path):
    wit = init(tmp_path)
    return tmp_path, wit, ObjectStore(wit)


def test_put_file_and_fsck_stay_well_under_file_size(tmp_path):
    root, wit, store = _setup(tmp_path)
    size = 16 * 1024 * 1024
    src = root / "groot.tif"
    src.write_bytes(os.urandom(size))  # vóór het meten gegenereerd

    tracemalloc.start()
    oid = store.put_file(src)
    _, put_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    tracemalloc.start()
    report = fsck(store)
    _, fsck_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert report.ok
    # streamend: piek blijft ruim onder de bestandsgrootte (chunkgrootte ~1 MB)
    assert put_peak < size // 4
    assert fsck_peak < size // 4
    assert store.get("blobs", oid)[:4] == src.read_bytes()[:4]


def test_witignore_excludes_from_add_and_status(tmp_path):
    root, wit, store = _setup(tmp_path)
    (root / ".witignore").write_text("*.tmp\nbuild/\n# commentaar\n")
    (root / "keep.txt").write_text("k")
    (root / "skip.tmp").write_text("s")
    (root / "build").mkdir()
    (root / "build" / "out.o").write_text("o")

    porcelain.add(wit, store, [str(root)])
    with Index(wit) as index:
        tracked = [e.path for e in index.entries()]
    assert "keep.txt" in tracked
    assert ".witignore" in tracked          # .witignore zelf is een gewoon bestand
    assert "skip.tmp" not in tracked
    assert "build/out.o" not in tracked

    (root / "later.tmp").write_text("x")
    with Index(wit) as index:
        status = compute_status(index, root)
    assert "later.tmp" not in status.untracked
    assert "skip.tmp" not in status.untracked


def test_explicit_add_overrides_ignore(tmp_path):
    root, wit, store = _setup(tmp_path)
    (root / ".witignore").write_text("*.tmp\n")
    (root / "expliciet.tmp").write_text("x")
    porcelain.add(wit, store, [str(root / "expliciet.tmp")])
    with Index(wit) as index:
        assert "expliciet.tmp" in [e.path for e in index.entries()]


def test_nested_witignore_only_applies_to_subtree(tmp_path):
    root, wit, store = _setup(tmp_path)
    # root-regel: globaal; submap-regel: alleen daar
    (root / ".witignore").write_text("*.tmp\n")
    sub = root / "sub"
    sub.mkdir()
    (sub / ".witignore").write_text("*.log\n")
    (root / "boven.log").write_text("x")       # niet genegeerd (regel is genest in sub/)
    (sub / "onder.log").write_text("y")         # genegeerd door sub/.witignore
    (sub / "onder.tmp").write_text("z")         # genegeerd door root-regel (globaal)
    (sub / "houden.txt").write_text("k")

    porcelain.add(wit, store, [str(root)])
    with Index(wit) as index:
        tracked = {e.path for e in index.entries()}
    assert "boven.log" in tracked               # root-niveau: sub-regel geldt hier niet
    assert "sub/houden.txt" in tracked
    assert "sub/onder.log" not in tracked       # geneste regel pakt zijn subboom
    assert "sub/onder.tmp" not in tracked        # root-regel blijft globaal


def test_ignore_pattern_matching():
    rules = IgnoreRules(["*.tmp", "build/", "/root-only.txt", "docs/*.pdf"])
    assert rules.match("a.tmp", False)
    assert rules.match("sub/b.tmp", False)          # geen slash -> elk niveau
    assert rules.match("build", True)               # mappatroon
    assert rules.match("build/out.o", False)        # bestand onder genegeerde map
    assert rules.match("root-only.txt", False)      # verankerd aan root
    assert not rules.match("sub/root-only.txt", False)
    assert rules.match("docs/handleiding.pdf", False)
    assert not rules.match("gewoon.txt", False)
