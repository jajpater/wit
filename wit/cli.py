"""wit command-line interface.

M0: ``init`` en ``fsck``, plus de debug-helpers ``hash-object`` en ``cat-object``
waarmee de object store (put/get) vanaf de commandline te bewijzen is.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .fsck import fsck
from .objects import KINDS, ObjectStore, hash_file
from .repo import find_wit, init


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wit")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="initialiseer een lege repository")
    p.add_argument("path", nargs="?", default=".")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("fsck", help="verifieer de object store")
    p.set_defaults(func=cmd_fsck)

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
