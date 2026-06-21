"""wit command-line interface.

M0: ``init`` en ``fsck``, plus de debug-helpers ``hash-object`` en ``cat-object``
waarmee de object store (put/get) vanaf de commandline te bewijzen is.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import porcelain, sync
from .commits import log, read_commit
from .fsck import fsck
from .gc import DEFAULT_GRACE_SECONDS, gc
from .index import Index
from .objects import KINDS, ObjectStore, hash_file
from .refs import head_ref, read_head
from .remote import make_remote
from .repo import (
    find_wit,
    init,
    read_config,
    read_shallow,
    read_sparse,
    set_remote,
    set_sparse,
)
from .status import compute_status


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
    added = porcelain.add(wit, ObjectStore(wit), args.paths)
    print(f"{added} bestand(en) toegevoegd")
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    wit = find_wit()
    removed = porcelain.rm(wit, ObjectStore(wit), args.paths, keep_file=args.cached)
    print(f"{removed} bestand(en) niet langer gevolgd")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    wit = find_wit()
    store = ObjectStore(wit)
    head = read_head(wit)
    head_tree = (
        porcelain.tree_map(store, read_commit(store, head)["tree"])
        if head else None
    )
    with Index(wit) as index:
        status = compute_status(index, wit.parent, head_tree)
    groups = (
        ("Conflicten (beide versies bewaard — kies, bewerk, voeg toe)", status.conflicts),
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
    try:
        commit_id = porcelain.commit(wit, ObjectStore(wit), args.message)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"[{head_ref(wit)} {commit_id[3:11]}] {args.message}")
    return 0


def cmd_checkout(args: argparse.Namespace) -> int:
    wit = find_wit()
    store = ObjectStore(wit)
    commit_id = args.commit or read_head(wit)
    if commit_id is None:
        print("niets om uit te checken (nog geen commits)", file=sys.stderr)
        return 1
    count = porcelain.checkout(wit, store, commit_id)
    print(f"{count} bestand(en) uitgecheckt")
    return 0


def _remote_path(args: argparse.Namespace, wit) -> str | None:
    return args.remote or read_config(wit).get("remote")


def _normalize_spec(spec: str) -> str:
    # Een kaal lokaal pad absoluut maken zodat het werkt vanuit een andere cwd;
    # scheme-specs (rclone:/server:/fs:) blijven ongemoeid.
    if ":" in spec.split("/", 1)[0]:
        return spec
    return str(Path(spec).resolve())


def cmd_clone(args: argparse.Namespace) -> int:
    spec = _normalize_spec(args.remote)
    wit = sync.clone(make_remote(spec), Path(args.dest))
    set_remote(wit, spec)
    print(f"gekloond naar {wit.parent}")
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    wit = find_wit()
    spec = _remote_path(args, wit)
    if not spec:
        print("geen remote opgegeven of geconfigureerd", file=sys.stderr)
        return 1
    try:
        head = sync.push(wit, ObjectStore(wit), make_remote(_normalize_spec(spec)))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"gepusht naar {spec}: {head[3:11]}")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    wit = find_wit()
    spec = _remote_path(args, wit)
    if not spec:
        print("geen remote opgegeven of geconfigureerd", file=sys.stderr)
        return 1
    try:
        result = sync.pull(wit, ObjectStore(wit), make_remote(_normalize_spec(spec)))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if result is None:
        print("remote is leeg")
        return 0
    head, conflicts = result
    print(f"bijgewerkt naar {head[3:11]}")
    if conflicts:
        print(f"{len(conflicts)} conflict(en) — beide versies bewaard:")
        for path in conflicts:
            print(f"    {path}")
    return 0


def cmd_sparse(args: argparse.Namespace) -> int:
    wit = find_wit()
    if args.action == "list":
        patterns = read_sparse(wit)
        print("\n".join(patterns) if patterns else "(geen sparse; volledige checkout)")
        return 0
    set_sparse(wit, args.patterns)
    head = read_head(wit)
    if head is not None:
        count = porcelain.checkout(wit, ObjectStore(wit), head)
        print(f"sparse-cone toegepast: {count} bestand(en) uitgecheckt")
    else:
        print("sparse-cone ingesteld")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .web import serve

    serve(find_wit(), host=args.host, port=args.port)
    return 0


def cmd_gc(args: argparse.Namespace) -> int:
    wit = find_wit()
    store = ObjectStore(wit)
    if args.keep is not None:
        report = porcelain.retain(wit, store, args.keep, grace_seconds=args.grace)
    else:
        report = gc(wit, store, grace_seconds=args.grace)
    print(
        f"{report.removed} verwijderd, {report.kept} behouden"
        + (f", {report.skipped_young} binnen grace-venster" if report.skipped_young else "")
    )
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    wit = find_wit()
    history = log(ObjectStore(wit), read_head(wit), read_shallow(wit))
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

    p = sub.add_parser("rm", help="haal bestanden uit beheer")
    p.add_argument("paths", nargs="+")
    p.add_argument(
        "--cached", action="store_true", help="alleen untracken, bestand laten staan"
    )
    p.set_defaults(func=cmd_rm)

    p = sub.add_parser("status", help="toon werkdir t.o.v. de index")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("commit", help="leg de staged toestand vast als commit")
    p.add_argument("-m", "--message", required=True)
    p.set_defaults(func=cmd_commit)

    p = sub.add_parser("log", help="toon de commit-historie (DAG)")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("serve", help="start de read-only webinterface")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("gc", help="ruim onbereikbare objecten op (mark/grace/sweep)")
    p.add_argument(
        "--grace", type=float, default=DEFAULT_GRACE_SECONDS,
        help="grace-venster in seconden (standaard ~2 weken)",
    )
    p.add_argument(
        "--keep", type=int, default=None,
        help="bewaar alleen de laatste N commits (retentie)",
    )
    p.set_defaults(func=cmd_gc)

    p = sub.add_parser("checkout", help="materialiseer een commit in de werkdir")
    p.add_argument("commit", nargs="?", help="commit-id (standaard: HEAD)")
    p.set_defaults(func=cmd_checkout)

    p = sub.add_parser("sparse", help="beheer de gedeeltelijke (sparse) checkout")
    p.add_argument("action", choices=("set", "list"))
    p.add_argument("patterns", nargs="*", help="padprefixen voor de cone")
    p.set_defaults(func=cmd_sparse)

    p = sub.add_parser("clone", help="kloon een remote naar een nieuwe map")
    p.add_argument("remote")
    p.add_argument("dest")
    p.set_defaults(func=cmd_clone)

    p = sub.add_parser("push", help="upload commits naar de remote")
    p.add_argument("remote", nargs="?", help="remote-pad (standaard: geconfigureerd)")
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("pull", help="haal commits van de remote (fast-forward)")
    p.add_argument("remote", nargs="?", help="remote-pad (standaard: geconfigureerd)")
    p.set_defaults(func=cmd_pull)

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
