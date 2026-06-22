"""Read-only web interface: browse commits, trees and files online.

Server-side rendered with the stdlib (``http.server``), no dependencies, no JS. Files
are served streaming from the object store (memory efficient, even for large
documents). Intentionally read-only: there are no write endpoints.

The resolution helpers (`resolve_commit`, `tree_listing`, `blob_entry`) are pure functions,
decoupled from HTTP, so they can be tested directly.
"""

from __future__ import annotations

import html
import mimetypes
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .commits import log, read_commit
from .i18n import _
from .objects import ObjectStore
from .refs import read_head
from .trees import read_tree

_CHUNK = 1024 * 1024


# -- pure resolutie-helpers --------------------------------------------------

def resolve_commit(wit: Path, ref: str) -> str | None:
    return read_head(wit) if ref == "HEAD" else ref


def _tree_at(store: ObjectStore, root_tree: str, subpath: str) -> str | None:
    tree = root_tree
    for comp in (c for c in subpath.split("/") if c):
        entry = read_tree(store, tree).get(comp)
        if entry is None or entry["type"] != "tree":
            return None
        tree = entry["hash"]
    return tree


def tree_listing(
    store: ObjectStore, commit_id: str, subpath: str = ""
) -> list[tuple[str, dict]] | None:
    """Sorted (name, entry) list of a directory inside a commit."""
    root = read_commit(store, commit_id)["tree"]
    tree = _tree_at(store, root, subpath)
    if tree is None:
        return None
    return sorted(read_tree(store, tree).items())


def blob_entry(store: ObjectStore, commit_id: str, path: str) -> dict | None:
    root = read_commit(store, commit_id)["tree"]
    comps = [c for c in path.split("/") if c]
    if not comps:
        return None
    parent = _tree_at(store, root, "/".join(comps[:-1]))
    if parent is None:
        return None
    entry = read_tree(store, parent).get(comps[-1])
    return entry if entry and entry["type"] == "blob" else None


# -- HTML --------------------------------------------------------------------

def _page(title: str, body: str) -> bytes:
    return (
        "<!doctype html><meta charset=utf-8>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:sans-serif;margin:2rem;max-width:50rem}"
        "a{text-decoration:none}li{margin:.2rem 0}"
        ".dir{font-weight:bold}.meta{color:#666;font-size:.9em}</style>"
        f"<h1>wit</h1>{body}"
    ).encode("utf-8")


def _breadcrumbs(commit_id: str, subpath: str) -> str:
    parts = [c for c in subpath.split("/") if c]
    crumbs = [f'<a href="/tree/{html.escape(commit_id)}/">/</a>']
    acc = ""
    for part in parts:
        acc = f"{acc}{part}/"
        crumbs.append(f'<a href="/tree/{html.escape(commit_id)}/{html.escape(acc)}">{html.escape(part)}</a>')
    return " ".join(crumbs)


def render_index(store: ObjectStore, wit: Path) -> bytes:
    head = read_head(wit)
    if head is None:
        return _page("wit", f"<p>{_('no commits yet')}</p>")
    rows = []
    for cid, commit in log(store, head):
        short = html.escape(cid[3:11])
        msg = html.escape(commit["message"])
        when = html.escape(commit["time"])
        rows.append(
            f'<li><a href="/commit/{html.escape(cid)}">{short}</a> '
            f'{msg} <span class="meta">{when}</span></li>'
        )
    body = (
        f'<p><a href="/tree/HEAD/">📂 {_("browse HEAD")}</a></p>'
        f"<h2>{_('commits')}</h2><ul>{''.join(rows)}</ul>"
    )
    return _page("wit", body)


def render_tree(store: ObjectStore, commit_id: str, subpath: str) -> bytes | None:
    listing = tree_listing(store, commit_id, subpath)
    if listing is None:
        return None
    items = []
    for name, entry in listing:
        href_path = f"{subpath}/{name}" if subpath else name
        if entry["type"] == "tree":
            items.append(
                f'<li class="dir"><a href="/tree/{html.escape(commit_id)}/'
                f'{html.escape(href_path)}">{html.escape(name)}/</a></li>'
            )
        else:
            size = entry.get("size", 0)
            items.append(
                f'<li><a href="/blob/{html.escape(commit_id)}/'
                f'{html.escape(href_path)}">{html.escape(name)}</a> '
                f'<span class="meta">{size} bytes</span></li>'
            )
    body = (
        f"<p>{_breadcrumbs(commit_id, subpath)}</p><ul>{''.join(items)}</ul>"
    )
    return _page(f"tree {subpath}", body)


def render_commit(store: ObjectStore, commit_id: str) -> bytes:
    commit = read_commit(store, commit_id)
    parents = " ".join(
        f'<a href="/commit/{html.escape(p)}">{html.escape(p[3:11])}</a>'
        for p in commit["parents"]
    )
    body = (
        f'<p class="meta">{html.escape(commit["time"])} · {html.escape(commit["host"])}</p>'
        f"<p>{html.escape(commit['message'])}</p>"
        f"<p>parents: {parents or '—'}</p>"
        f'<p><a href="/tree/{html.escape(commit_id)}/">📂 {_("browse this commit")}</a></p>'
    )
    return _page(f"commit {commit_id[3:11]}", body)


# -- HTTP --------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # stil
        pass

    @property
    def _store(self) -> ObjectStore:
        return self.server.store  # type: ignore[attr-defined]

    @property
    def _wit(self) -> Path:
        return self.server.wit  # type: ignore[attr-defined]

    def _send_html(self, body: bytes, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        parts = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
        try:
            if not parts:
                self._send_html(render_index(self._store, self._wit))
            elif parts[0] == "commit" and len(parts) == 2:
                self._send_html(render_commit(self._store, parts[1]))
            elif parts[0] == "tree" and len(parts) >= 2:
                commit = resolve_commit(self._wit, parts[1]) or parts[1]
                page = render_tree(self._store, commit, "/".join(parts[2:]))
                if page is None:
                    self._send_html(_page("404", f"<p>{_('not found')}</p>"), 404)
                else:
                    self._send_html(page)
            elif parts[0] == "blob" and len(parts) >= 3:
                commit = resolve_commit(self._wit, parts[1]) or parts[1]
                self._serve_blob(commit, "/".join(parts[2:]), "download" in query)
            else:
                self._send_html(_page("404", f"<p>{_('not found')}</p>"), 404)
        except (KeyError, ValueError):
            self._send_html(_page("404", f"<p>{_('not found')}</p>"), 404)

    def _serve_blob(self, commit_id: str, path: str, download: bool) -> None:
        entry = blob_entry(self._store, commit_id, path)
        if entry is None:
            self._send_html(_page("404", f"<p>{_('not found')}</p>"), 404)
            return
        name = path.rsplit("/", 1)[-1]
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(entry.get("size", 0)))
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.end_headers()
        with open(self._store.path_for("blobs", entry["hash"]), "rb") as src:
            while chunk := src.read(_CHUNK):
                self.wfile.write(chunk)


def make_server(wit: Path, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), _Handler)
    server.wit = Path(wit)  # type: ignore[attr-defined]
    server.store = ObjectStore(wit)  # type: ignore[attr-defined]
    return server


def serve(wit: Path, host: str = "127.0.0.1", port: int = 8000) -> None:
    server = make_server(wit, host, port)
    print(_("wit web interface at http://{host}:{port}  (Ctrl-C to stop)").format(host=host, port=port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
