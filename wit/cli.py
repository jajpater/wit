"""wit command-line interface.

M0: ``init`` and ``fsck``, plus debug helpers ``hash-object`` and ``cat-object``
to prove the object store (put/get) from the commandline.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import porcelain, sync
from .i18n import _
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
    print(_("empty wit repository initialized in {wit}").format(wit=wit))
    return 0


def cmd_fsck(args: argparse.Namespace) -> int:
    report = fsck(_store())
    print(_("{count} object(s) checked").format(count=report.checked))
    if report.stray_tmp:
        print(_("{count} stray tmp file(s) cleaned up").format(count=report.stray_tmp))
    if report.corrupt:
        print(_("CORRUPT: {count} object(s):").format(count=len(report.corrupt)), file=sys.stderr)
        for oid in report.corrupt:
            print(f"  {oid}", file=sys.stderr)
        return 1
    print(_("ok"))
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
    print(_("{count} file(s) added").format(count=added))
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    wit = find_wit()
    removed = porcelain.rm(wit, ObjectStore(wit), args.paths, keep_file=args.cached)
    print(_("{count} file(s) no longer tracked").format(count=removed))
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
        (_("Conflicts (both versions kept — choose, edit, add)"), status.conflicts),
        (_("Staged (added)"), status.staged),
        (_("Staged (deleted)"), status.staged_deleted),
        (_("Modified (not staged)"), status.modified),
        (_("Deleted (not staged)"), status.deleted),
        (_("Untracked"), status.untracked),
    )
    if status.clean and not status.has_staged:
        print(_("working directory clean, nothing added"))
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
        print(_("nothing to checkout (no commits yet)"), file=sys.stderr)
        return 1
    count = porcelain.checkout(wit, store, commit_id)
    print(_("{count} file(s) checked out").format(count=count))
    return 0


def _remote_path(args: argparse.Namespace, wit) -> str | None:
    return args.remote or read_config(wit).get("remote")


def _normalize_spec(spec: str) -> str:
    # Make a bare local path absolute so it works from another cwd;
    # scheme-specs (rclone:/server:/fs:) are left untouched.
    if ":" in spec.split("/", 1)[0]:
        return spec
    return str(Path(spec).resolve())


def cmd_clone(args: argparse.Namespace) -> int:
    spec = _normalize_spec(args.remote)
    wit = sync.clone(make_remote(spec), Path(args.dest))
    set_remote(wit, spec)
    print(_("cloned to {dest}").format(dest=wit.parent))
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    wit = find_wit()
    spec = _remote_path(args, wit)
    if not spec:
        print(_("no remote specified or configured"), file=sys.stderr)
        return 1
    try:
        head = sync.push(wit, ObjectStore(wit), make_remote(_normalize_spec(spec)))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(_("pushed to {spec}: {head}").format(spec=spec, head=head[3:11]))
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    wit = find_wit()
    spec = _remote_path(args, wit)
    if not spec:
        print(_("no remote specified or configured"), file=sys.stderr)
        return 1
    try:
        result = sync.pull(wit, ObjectStore(wit), make_remote(_normalize_spec(spec)))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if result is None:
        print(_("remote is empty"))
        return 0
    head, conflicts = result
    print(_("updated to {head}").format(head=head[3:11]))
    if conflicts:
        print(_("{count} conflict(s) — both versions kept:").format(count=len(conflicts)))
        for path in conflicts:
            print(f"    {path}")
    return 0


def cmd_sparse(args: argparse.Namespace) -> int:
    wit = find_wit()
    if args.action == "list":
        patterns = read_sparse(wit)
        print("\n".join(patterns) if patterns else _("(no sparse cone; full checkout)"))
        return 0
    set_sparse(wit, args.patterns)
    head = read_head(wit)
    if head is not None:
        count = porcelain.checkout(wit, ObjectStore(wit), head)
        print(_("sparse cone applied: {count} file(s) checked out").format(count=count))
    else:
        print(_("sparse cone configured"))
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
    young_msg = _(", {count} within grace window").format(count=report.skipped_young) if report.skipped_young else ""
    print(_("{removed} removed, {kept} kept").format(removed=report.removed, kept=report.kept) + young_msg)
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    wit = find_wit()
    history = log(ObjectStore(wit), read_head(wit), read_shallow(wit))
    if not history:
        print(_("no commits yet"))
        return 0
    for commit_id, commit in history:
        print(_("commit {commit_id}").format(commit_id=commit_id))
        if len(commit["parents"]) > 1:
            print("Merge: " + " ".join(p[3:11] for p in commit["parents"]))
        print(_("Date:  {time}   Host: {host}").format(time=commit['time'], host=commit['host']))
        print(f"\n    {commit['message']}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wit")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="initialize an empty repository")
    p.add_argument("path", nargs="?", default=".")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("fsck", help="verify the object store")
    p.set_defaults(func=cmd_fsck)

    p = sub.add_parser("add", help="start tracking files")
    p.add_argument("paths", nargs="+")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("rm", help="stop tracking files")
    p.add_argument("paths", nargs="+")
    p.add_argument(
        "--cached", action="store_true", help="untrack only, keep file on disk"
    )
    p.set_defaults(func=cmd_rm)

    p = sub.add_parser("status", help="show working tree status compared to index")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("commit", help="record staged state as a commit")
    p.add_argument("-m", "--message", required=True)
    p.set_defaults(func=cmd_commit)

    p = sub.add_parser("log", help="show commit history (DAG)")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("serve", help="start the read-only web interface")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("gc", help="clean up unreachable objects (mark/grace/sweep)")
    p.add_argument(
        "--grace", type=float, default=DEFAULT_GRACE_SECONDS,
        help="grace window in seconds (default ~2 weeks)",
    )
    p.add_argument(
        "--keep", type=int, default=None,
        help="retain only the last N commits",
    )
    p.set_defaults(func=cmd_gc)

    p = sub.add_parser("checkout", help="materialize a commit in the working directory")
    p.add_argument("commit", nargs="?", help="commit-id (default: HEAD)")
    p.set_defaults(func=cmd_checkout)

    p = sub.add_parser("sparse", help="manage partial (sparse) checkout")
    p.add_argument("action", choices=("set", "list"))
    p.add_argument("patterns", nargs="*", help="path prefixes for the cone")
    p.set_defaults(func=cmd_sparse)

    p = sub.add_parser("clone", help="clone a remote into a new directory")
    p.add_argument("remote")
    p.add_argument("dest")
    p.set_defaults(func=cmd_clone)

    p = sub.add_parser("push", help="upload commits to the remote")
    p.add_argument("remote", nargs="?", help="remote path (default: configured remote)")
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("pull", help="fetch commits from the remote (fast-forward)")
    p.add_argument("remote", nargs="?", help="remote path (default: configured remote)")
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser("hash-object", help="hash (and optionally write) a file")
    p.add_argument("file")
    p.add_argument("-w", "--write", action="store_true", help="save as blob")
    p.set_defaults(func=cmd_hash_object)

    p = sub.add_parser("cat-object", help="write object bytes to stdout")
    p.add_argument("kind", choices=KINDS)
    p.add_argument("oid")
    p.set_defaults(func=cmd_cat_object)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
