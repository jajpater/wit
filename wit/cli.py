"""wit command-line interface.

M0: ``init`` en ``fsck``, plus de debug-helpers ``hash-object`` en ``cat-object``
waarmee de object store (put/get) vanaf de commandline te bewijzen is.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .commits import create_commit, log
from .fsck import fsck
from .index import Index, IndexEntry
from .objects import KINDS, ObjectStore, hash_file
from .refs import head_ref, read_head, update_ref
from .repo import find_wit, init
from .status import compute_status
from .trees import build_tree
from .worktree import rel_path, walk_files


def _store() -> ObjectStore:
    return ObjectStore(find_wit())


def cmd_init(args: argparse.Namespace) -> int:
    wit = init(Path(args.path))
    print(f"lege wit-repository geïnitialiseerd in {wit}")
    return 0


def cmd_fsck(args: argparse.Namespace) -> int:
    report = fsck(_store())
    print(f"{report.checked} object(en) gecontroleerd")
    if report.stray_tmp:
        print(f"{report.stray_tmp} verweesde tmp-bestand(en) opgeruimd")
    if report.corrupt:
        print(f"CORRUPT: {len(report.corrupt)} object(en):", file=sys.stderr)
        for oid in report.corrupt:
            print(f"  {oid}", file=sys.stderr)
        return 1
    print("ok")
    return 0


def cmd_hash_object(args: argparse.Namespace) -> int:
    src = Path(args.file)
    oid = _store().put_file(src, kind="blobs") if args.write else hash_file(src)
    print(oid)
    return 0


def cmd_cat_object(args: argparse.Namespace) -> int:
    sys.stdout.buffer.write(_store().get(args.kind, args.oid))
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    wit = find_wit()
    root = wit.parent
    store = ObjectStore(wit)
    added = 0
    with Index(wit) as index:
        for raw in args.paths:
            for path in walk_files(Path(raw).resolve()):
                rel = rel_path(path, root)
                oid = store.put_file(path, kind="blobs")
                st = path.stat()
                index.put_entry(IndexEntry(
                    path=rel, hash=oid, mode=st.st_mode, size=st.st_size,
                    mtime_ns=st.st_mtime_ns, ctime_ns=st.st_ctime_ns,
                    device=st.st_dev, inode=st.st_ino, staged=1,
                ))
                added += 1
    print(f"{added} bestand(en) toegevoegd")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    wit = find_wit()
    with Index(wit) as index:
        status = compute_status(index, wit.parent)
    groups = (
        ("Gewijzigd (niet opnieuw toegevoegd)", status.modified),
        ("Toegevoegd (staged)", status.staged),
        ("Verwijderd", status.deleted),
        ("Niet gevolgd", status.untracked),
    )
    if status.clean and not status.staged:
        print("werkdirectory schoon, niets toegevoegd")
        return 0
    for title, paths in groups:
        if not paths:
            continue
        print(f"{title}:")
        for rel in paths:
            print(f"    {rel}")
    return 0


def cmd_commit(args: argparse.Namespace) -> int:
    wit = find_wit()
    store = ObjectStore(wit)
    with Index(wit) as index:
        entries = index.entries()
    if not entries:
        print("niets om te committen (index is leeg)", file=sys.stderr)
        return 1
    tree = build_tree(entries, store)
    parents = [head] if (head := read_head(wit)) else []
    commit_id = create_commit(store, tree, parents, args.message)
    update_ref(wit, head_ref(wit), commit_id)
    print(f"[{head_ref(wit)} {commit_id[3:11]}] {args.message}")
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    wit = find_wit()
    history = log(ObjectStore(wit), read_head(wit))
    if not history:
        print("nog geen commits")
        return 0
    for commit_id, commit in history:
        print(f"commit {commit_id}")
        if len(commit["parents"]) > 1:
            print("Merge: " + " ".join(p[3:11] for p in commit["parents"]))
        print(f"Datum:  {commit['time']}   Host: {commit['host']}")
        print(f"\n    {commit['message']}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wit")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="initialiseer een lege repository")
    p.add_argument("path", nargs="?", default=".")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("fsck", help="verifieer de object store")
    p.set_defaults(func=cmd_fsck)

    p = sub.add_parser("add", help="neem bestanden onder beheer")
    p.add_argument("paths", nargs="+")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("status", help="toon werkdir t.o.v. de index")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("commit", help="leg de staged toestand vast als commit")
    p.add_argument("-m", "--message", required=True)
    p.set_defaults(func=cmd_commit)

    p = sub.add_parser("log", help="toon de commit-historie (DAG)")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("hash-object", help="hash (en met -w: bewaar) een bestand")
    p.add_argument("file")
    p.add_argument("-w", "--write", action="store_true", help="bewaar als blob")
    p.set_defaults(func=cmd_hash_object)

    p = sub.add_parser("cat-object", help="schrijf object-bytes naar stdout")
    p.add_argument("kind", choices=KINDS)
    p.add_argument("oid")
    p.set_defaults(func=cmd_cat_object)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
