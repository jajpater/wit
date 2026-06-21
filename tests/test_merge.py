"""M6: atomaire ref-CAS (flock) + reconcile van divergente historie tot een merge."""

import threading

from wit import porcelain, sync
from wit.commits import log, read_commit
from wit.merge import merge_base
from wit.objects import ObjectStore
from wit.remote import FilesystemRemote, WitServerRemote
from wit.repo import init


def _write(root, rel, data):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def _base_remote(tmp_path):
    """Een remote met één basiscommit; geeft (remote-pad)."""
    src = tmp_path / "src"
    src.mkdir()
    wit = init(src)
    store = ObjectStore(wit)
    _write(src, "gedeeld.txt", b"basis\n")
    porcelain.add(wit, store, [str(src)])
    porcelain.commit(wit, store, "basis", time="2026-01-01T00:00:00.000000Z")
    remote = tmp_path / "remote"
    sync.push(wit, store, FilesystemRemote(remote))
    return remote


def test_flock_cas_exactly_one_winner(tmp_path):
    # 20 threads racen om dezelfde ref None->eigen id; precies één mag winnen.
    remote = WitServerRemote(tmp_path / "remote")
    winners: list[str] = []
    lock = threading.Lock()
    start = threading.Barrier(20)

    def attempt(i: int) -> None:
        start.wait()
        if remote.compare_and_swap_ref("refs/heads/main", None, f"b3:{i:064d}"):
            with lock:
                winners.append(str(i))

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(winners) == 1
    assert remote.read_ref("refs/heads/main") is not None


def test_reconcile_merges_independent_changes(tmp_path):
    remote = _base_remote(tmp_path)

    a = sync.clone(FilesystemRemote(remote), tmp_path / "a")
    b = sync.clone(FilesystemRemote(remote), tmp_path / "b")

    # A wijzigt bestand X en pusht (fast-forward)
    _write(tmp_path / "a", "alfa.txt", b"door a")
    porcelain.add(a, ObjectStore(a), [str(tmp_path / "a")])
    a_head = porcelain.commit(a, ObjectStore(a), "a", time="2026-01-02T00:00:00.000000Z")
    sync.push(a, ObjectStore(a), FilesystemRemote(remote))

    # B wijzigt een ANDER bestand Y en commit (nu divergent)
    _write(tmp_path / "b", "beta.txt", b"door b")
    porcelain.add(b, ObjectStore(b), [str(tmp_path / "b")])
    b_head = porcelain.commit(b, ObjectStore(b), "b", time="2026-01-03T00:00:00.000000Z")

    # B pullt -> reconcile tot merge-commit, geen conflict (andere bestanden)
    head, conflicts = sync.pull(b, ObjectStore(b), FilesystemRemote(remote))
    assert conflicts == []
    merge = read_commit(ObjectStore(b), head)
    assert set(merge["parents"]) == {b_head, a_head}      # twee parents, geen verlies
    # beide wijzigingen aanwezig in de werkdir
    assert (tmp_path / "b" / "alfa.txt").read_bytes() == b"door a"
    assert (tmp_path / "b" / "beta.txt").read_bytes() == b"door b"
    # beide oorspronkelijke commits nog bereikbaar
    ids = {cid for cid, _ in log(ObjectStore(b), head)}
    assert {a_head, b_head} <= ids

    # de merge kan nu naar de remote (fast-forward)
    sync.push(b, ObjectStore(b), FilesystemRemote(remote))
    assert FilesystemRemote(remote).read_ref("refs/heads/main") == head


def test_reconcile_same_path_conflict_keeps_both(tmp_path):
    remote = _base_remote(tmp_path)
    a = sync.clone(FilesystemRemote(remote), tmp_path / "a")
    b = sync.clone(FilesystemRemote(remote), tmp_path / "b")

    # beide wijzigen HETZELFDE bestand verschillend
    _write(tmp_path / "a", "gedeeld.txt", b"versie van a")
    porcelain.add(a, ObjectStore(a), [str(tmp_path / "a")])
    porcelain.commit(a, ObjectStore(a), "a", time="2026-01-02T00:00:00.000000Z")
    sync.push(a, ObjectStore(a), FilesystemRemote(remote))

    _write(tmp_path / "b", "gedeeld.txt", b"versie van b")
    porcelain.add(b, ObjectStore(b), [str(tmp_path / "b")])
    porcelain.commit(b, ObjectStore(b), "b", time="2026-01-03T00:00:00.000000Z")

    head, conflicts = sync.pull(b, ObjectStore(b), FilesystemRemote(remote))
    assert conflicts == ["gedeeld.txt"]
    # onze versie op de oorspronkelijke naam, die van hen onder een conflict-naam
    assert (tmp_path / "b" / "gedeeld.txt").read_bytes() == b"versie van b"
    conflict_files = list((tmp_path / "b").glob("gedeeld.conflict-*.txt"))
    assert len(conflict_files) == 1
    assert conflict_files[0].read_bytes() == b"versie van a"


def test_conflict_shows_in_status_until_resolved(tmp_path):
    from wit.index import Index
    from wit.status import compute_status

    remote = _base_remote(tmp_path)
    a = sync.clone(FilesystemRemote(remote), tmp_path / "a")
    b = sync.clone(FilesystemRemote(remote), tmp_path / "b")

    _write(tmp_path / "a", "gedeeld.txt", b"versie van a")
    porcelain.add(a, ObjectStore(a), [str(tmp_path / "a")])
    porcelain.commit(a, ObjectStore(a), "a", time="2026-01-02T00:00:00.000000Z")
    sync.push(a, ObjectStore(a), FilesystemRemote(remote))

    _write(tmp_path / "b", "gedeeld.txt", b"versie van b")
    porcelain.add(b, ObjectStore(b), [str(tmp_path / "b")])
    porcelain.commit(b, ObjectStore(b), "b", time="2026-01-03T00:00:00.000000Z")
    sync.pull(b, ObjectStore(b), FilesystemRemote(remote))

    # status toont het conflict en is dus niet schoon
    with Index(b) as index:
        st = compute_status(index, b.parent)
    assert st.conflicts == ["gedeeld.txt"]
    assert not st.clean

    # gebruiker lost op: kiest een versie, ruimt het conflict-bestand op, add + commit
    for f in (tmp_path / "b").glob("gedeeld.conflict-*.txt"):
        f.unlink()
    (tmp_path / "b" / "gedeeld.txt").write_bytes(b"samengevoegd")
    porcelain.add(b, ObjectStore(b), [str(tmp_path / "b" / "gedeeld.txt")])

    with Index(b) as index:
        st = compute_status(index, b.parent)
    assert st.conflicts == []   # opgelost: weg uit status


def test_merge_base_finds_common_ancestor(tmp_path):
    from wit.commits import create_commit
    from wit.trees import build_tree

    src = tmp_path / "src"
    src.mkdir()
    wit = init(src)
    store = ObjectStore(wit)
    _write(src, "x", b"1")
    porcelain.add(wit, store, [str(src)])
    base = porcelain.commit(wit, store, "base", time="2026-01-01T00:00:00.000000Z")
    base_tree = read_commit(store, base)["tree"]

    # twee onafhankelijke takken vanaf dezelfde base
    left = create_commit(store, base_tree, [base], "left", time="2026-01-02T00:00:00.000000Z")
    right = create_commit(store, base_tree, [base], "right", time="2026-01-03T00:00:00.000000Z")

    assert merge_base(store, left, right) == base
    assert merge_base(store, left, base) == base   # base is voorouder van left
