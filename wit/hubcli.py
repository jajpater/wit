"""``wit-hub`` — administer a multi-repo host (see ARCHITECTURE-hub.md).

A thin command layer over ``Hub`` (repo lifecycle), ``access`` (tokens), and
``hubserver`` (the HTTP router). The hub root is taken from ``--root``, else
``$WIT_HUB_ROOT``, else the current directory.
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from pathlib import Path

from .access import add_token, load_tokens
from .hub import Hub
from .i18n import _


def _root(args: argparse.Namespace) -> Path:
    return Path(args.root or os.environ.get("WIT_HUB_ROOT") or ".")


def _split_slug(slug: str) -> tuple[str, str]:
    if slug.count("/") != 1:
        raise SystemExit(_("expected owner/name, got: {slug}").format(slug=slug))
    owner, name = slug.split("/")
    return owner, name


def cmd_init(args: argparse.Namespace) -> int:
    hub = Hub.init(_root(args))
    print(_("empty hub initialized in {root}").format(root=hub.root))
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    owner, name = _split_slug(args.slug)
    visibility = "public" if args.public else "private"
    try:
        ref = Hub(_root(args)).create(owner, name, visibility)
    except (FileExistsError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(_("created {slug} ({vis})").format(slug=ref.slug, vis=ref.visibility))
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    owner, name = _split_slug(args.slug)
    try:
        Hub(_root(args)).delete(owner, name)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(_("removed {slug}").format(slug=f"{owner}/{name}"))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    repos = Hub(_root(args)).list()
    if not repos:
        print(_("no repositories"))
        return 0
    for ref in repos:
        print(f"{ref.slug}  ({ref.visibility})")
    return 0


def cmd_visibility(args: argparse.Namespace) -> int:
    owner, name = _split_slug(args.slug)
    try:
        ref = Hub(_root(args)).set_visibility(owner, name, args.visibility)
    except (FileNotFoundError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(_("{slug} is now {vis}").format(slug=ref.slug, vis=ref.visibility))
    return 0


def cmd_token(args: argparse.Namespace) -> int:
    root = _root(args)
    if args.token_action == "add":
        token = add_token(root, args.owner, args.token)
        print(_("token for {owner}: {token}").format(owner=args.owner, token=token))
        print(_("clients use it via:  export WIT_TOKEN={token}").format(token=token))
        return 0
    # list
    tokens = load_tokens(root)
    if not tokens:
        print(_("no tokens"))
        return 0
    for tok, owner in tokens.items():
        print(f"{owner}\t{tok[:8]}…")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .hubserver import serve

    root = _root(args)
    host, port = args.host, args.port
    cfg = root / "hub.toml"
    if cfg.exists():
        data = tomllib.loads(cfg.read_text())
        host = host or data.get("host", "127.0.0.1")
        port = port or int(data.get("port", 8080))
    serve(root, host=host or "127.0.0.1", port=port or 8080)
    return 0


def cmd_gc(args: argparse.Namespace) -> int:
    hub = Hub(_root(args))
    if args.slug:
        owner, name = _split_slug(args.slug)
        ref = hub.resolve(owner, name)
        if ref is None:
            print(_("no such repo: {slug}").format(slug=args.slug), file=sys.stderr)
            return 1
        targets = [ref]
    else:
        targets = hub.list()
    for ref in targets:
        report = hub.remote_for(ref).gc(grace_seconds=args.grace)
        print(_("{slug}: {removed} removed, {kept} kept").format(
            slug=ref.slug, removed=report.removed, kept=report.kept))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wit-hub")
    parser.add_argument(
        "--root", help="hub root (default: $WIT_HUB_ROOT or .)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="initialize an empty hub")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("create", help="create a hosted repository (owner/name)")
    p.add_argument("slug")
    p.add_argument("--public", action="store_true", help="anyone may read/clone")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("rm", help="delete a hosted repository (owner/name)")
    p.add_argument("slug")
    p.set_defaults(func=cmd_rm)

    p = sub.add_parser("list", help="list hosted repositories")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("visibility", help="set a repo's visibility (owner/name)")
    p.add_argument("slug")
    p.add_argument("visibility", choices=("public", "private"))
    p.set_defaults(func=cmd_visibility)

    p = sub.add_parser("token", help="manage access tokens")
    tsub = p.add_subparsers(dest="token_action", required=True)
    pa = tsub.add_parser("add", help="create a token for an owner")
    pa.add_argument("owner")
    pa.add_argument("--token", help="use this exact token instead of generating one")
    tsub.add_parser("list", help="list tokens (owner + prefix)")
    p.set_defaults(func=cmd_token)

    p = sub.add_parser("serve", help="start the hub HTTP server")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("gc", help="garbage-collect one repo or all")
    p.add_argument("slug", nargs="?", help="owner/name (default: all repos)")
    p.add_argument("--grace", type=float, default=None,
                   help="grace window in seconds")
    p.set_defaults(func=cmd_gc)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
