"""HTTP server for a hub: the router + the server side of object transport.

Two route families under ``/<owner>/<name>/`` (see ARCHITECTURE-hub.md):

* **Transport** (read-write, drives ``wit clone/push/pull`` via ``HttpRemote``):
  ``objects/<kind>/<oid>`` (HEAD/GET/PUT), ``objects/<kind>/`` (GET list),
  ``refs/heads/<branch>`` (GET read, POST compare-and-swap).
* **Viewer** (read-only): ``/`` lists repos; ``/<owner>/<name>/`` reuses the
  single-repo render functions from ``web.py`` with a per-repo URL ``base``.

The transport endpoints delegate to the repo's ``WitServerRemote``, so the ref
compare-and-swap still runs under its ``flock`` — atomicity stays in one place.

Access policy is a TODO: this skeleton serves every repo to everyone. Put the hub
behind a reverse proxy, or add a principal check here, before exposing it.
"""

from __future__ import annotations

import html
import json
import mimetypes
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import web
from .hub import Hub, RepoRef
from .i18n import _
from .objects import KINDS

_CHUNK = 1024 * 1024


def render_repo_list(hub: Hub) -> bytes:
    items = []
    for ref in hub.list():
        slug = html.escape(ref.slug)
        vis = html.escape(ref.visibility)
        items.append(
            f'<li><a href="/{slug}/">{slug}</a> '
            f'<span class="meta">{vis}</span></li>'
        )
    body = f"<h2>{_('repositories')}</h2><ul>{''.join(items) or '—'}</ul>"
    return web._page("wit hub", body)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # stil
        pass

    @property
    def _hub(self) -> Hub:
        return self.server.hub  # type: ignore[attr-defined]

    # -- helpers ----------------------------------------------------------

    def _parts(self) -> tuple[list[str], dict]:
        parsed = urllib.parse.urlparse(self.path)
        parts = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
        return parts, urllib.parse.parse_qs(parsed.query)

    def _resolve(self, parts: list[str]) -> tuple[RepoRef, list[str]] | None:
        """Resolve ``/<owner>/<name>/...`` to (repo, rest) or send 404."""
        if len(parts) < 2:
            self._send_text("not found", 404)
            return None
        ref = self._hub.resolve(parts[0], parts[1])
        if ref is None:
            self._send_text("not found", 404)
            return None
        return ref, parts[2:]

    def _send_text(self, text: str, code: int = 200) -> None:
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_html(self, body: bytes, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- GET: viewer + object download/list + ref read --------------------

    def do_GET(self) -> None:
        parts, query = self._parts()
        if not parts:
            self._send_html(render_repo_list(self._hub))
            return
        resolved = self._resolve(parts)
        if resolved is None:
            return
        ref, rest = resolved
        try:
            if rest and rest[0] == "objects":
                self._get_object(ref, rest)
            elif rest and rest[0] == "refs":
                self._get_ref(ref, rest)
            else:
                self._view(ref, rest, "download" in query)
        except (KeyError, ValueError):
            self._send_text("not found", 404)

    def _get_object(self, ref: RepoRef, rest: list[str]) -> None:
        store = self._hub.store_for(ref)
        # objects/<kind>/  -> listing ;  objects/<kind>/<oid> -> bytes
        if len(rest) == 2 or (len(rest) == 3 and rest[2] == ""):
            kind = rest[1]
            if kind not in KINDS:
                self._send_text("not found", 404)
                return
            listing = "".join(f"{oid}\n" for oid in store.iter_objects(kind))
            self._send_text(listing)
            return
        if len(rest) != 3:
            self._send_text("not found", 404)
            return
        kind, oid = rest[1], rest[2]
        if kind not in KINDS or not store.has(kind, oid):
            self._send_text("not found", 404)
            return
        path = store.path_for(kind, oid)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(path.stat().st_size))
        self.end_headers()
        with open(path, "rb") as src:
            while chunk := src.read(_CHUNK):
                self.wfile.write(chunk)

    def _get_ref(self, ref: RepoRef, rest: list[str]) -> None:
        remote = self._hub.remote_for(ref)
        value = remote.read_ref("/".join(rest))
        self._send_text((value or "") + ("\n" if value else ""))

    def _view(self, ref: RepoRef, rest: list[str], download: bool) -> None:
        store = self._hub.store_for(ref)
        base = f"/{ref.owner}/{ref.name}"
        if not rest:
            self._send_html(web.render_index(store, ref.path, base))
        elif rest[0] == "commit" and len(rest) == 2:
            self._send_html(web.render_commit(store, rest[1], base))
        elif rest[0] == "tree" and len(rest) >= 2:
            commit = web.resolve_commit(ref.path, rest[1]) or rest[1]
            page = web.render_tree(store, commit, "/".join(rest[2:]), base)
            self._send_html(page or web._page("404", "not found"),
                            200 if page else 404)
        elif rest[0] == "blob" and len(rest) >= 3:
            commit = web.resolve_commit(ref.path, rest[1]) or rest[1]
            self._serve_blob(store, commit, "/".join(rest[2:]), download)
        else:
            self._send_text("not found", 404)

    def _serve_blob(self, store, commit_id, path, download) -> None:
        entry = web.blob_entry(store, commit_id, path)
        if entry is None:
            self._send_text("not found", 404)
            return
        name = path.rsplit("/", 1)[-1]
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(entry.get("size", 0)))
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.end_headers()
        with open(store.path_for("blobs", entry["hash"]), "rb") as src:
            while chunk := src.read(_CHUNK):
                self.wfile.write(chunk)

    # -- HEAD: object existence ------------------------------------------

    def do_HEAD(self) -> None:
        parts, _q = self._parts()
        resolved = self._resolve(parts)
        if resolved is None:
            return
        ref, rest = resolved
        if len(rest) == 3 and rest[0] == "objects":
            store = self._hub.store_for(ref)
            ok = rest[1] in KINDS and store.has(rest[1], rest[2])
            self._send_text("", 200 if ok else 404)
        else:
            self._send_text("", 404)

    # -- PUT: object upload ----------------------------------------------

    def do_PUT(self) -> None:
        parts, _q = self._parts()
        resolved = self._resolve(parts)
        if resolved is None:
            return
        ref, rest = resolved
        if len(rest) != 3 or rest[0] != "objects" or rest[1] not in KINDS:
            self._send_text("bad request", 400)
            return
        kind, oid = rest[1], rest[2]
        length = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(length)
        store = self._hub.store_for(ref)
        stored = store.put(kind, data)  # re-hashes -> verifies in transit
        if stored != oid:
            store._path(kind, stored).unlink(missing_ok=True)
            self._send_text("hash mismatch", 400)
            return
        self._send_text("", 201)

    # -- POST: ref compare-and-swap --------------------------------------

    def do_POST(self) -> None:
        parts, _q = self._parts()
        resolved = self._resolve(parts)
        if resolved is None:
            return
        ref, rest = resolved
        if not rest or rest[0] != "refs":
            self._send_text("bad request", 400)
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._send_text("bad request", 400)
            return
        remote = self._hub.remote_for(ref)
        ok = remote.compare_and_swap_ref(
            "/".join(rest), payload.get("expected"), payload["new"])
        body = json.dumps({"ok": bool(ok)}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_server(
    root: Path, host: str = "127.0.0.1", port: int = 8080
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), _Handler)
    server.hub = Hub(root)  # type: ignore[attr-defined]
    return server


def serve(root: Path, host: str = "127.0.0.1", port: int = 8080) -> None:
    server = make_server(root, host, port)
    print(_("wit hub at http://{host}:{port}  (Ctrl-C to stop)").format(
        host=host, port=port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
