"""HTTP server for a hub: the router + the server side of object transport.

Two route families under ``/<owner>/<name>/`` (see ARCHITECTURE-hub.md):

* **Transport** (read-write, drives ``wit clone/push/pull`` via ``HttpRemote``):
  ``objects/<kind>/<oid>`` (HEAD/GET/PUT), ``objects/<kind>/`` (GET list),
  ``refs/heads/<branch>`` (GET read, POST compare-and-swap).
* **Viewer** (read-only): ``/`` lists repos; ``/<owner>/<name>/`` reuses the
  single-repo render functions from ``web.py`` with a per-repo URL ``base``.
* **Lifecycle**: ``PUT /<owner>/<name>`` creates a repo (idempotent), gated by the
  same ``can_write`` check as a push — this is what auto-create-on-push and
  ``wit-hub create <url>`` drive.

The transport endpoints delegate to the repo's ``WitServerRemote``, so the ref
compare-and-swap still runs under its ``flock`` — atomicity stays in one place.

Access policy is a TODO: this skeleton serves every repo to everyone. Put the hub
behind a reverse proxy, or add a principal check here, before exposing it.
"""

from __future__ import annotations

import html
import json
import mimetypes
import tomllib
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import porcelain, web, wire
from .access import AccessPolicy, Principal
from .commits import read_commit
from .hub import Hub, RepoRef
from .i18n import _
from .objects import KINDS
from .refs import read_head

_CHUNK = 1024 * 1024


def _repo_meta(hub: Hub, ref: RepoRef) -> dict:
    """Description + last commit + file count for the repo overview card."""
    description = ""
    meta = ref.path / "repo.toml"
    if meta.exists():
        try:
            description = tomllib.loads(meta.read_text()).get("description", "")
        except (OSError, tomllib.TOMLDecodeError):
            pass
    info = {"description": description, "short": None, "date": None, "files": 0}
    store = hub.store_for(ref)
    head = read_head(ref.path)
    if head is not None:
        commit = read_commit(store, head)
        info["short"] = head[3:11]
        info["date"] = commit["time"][:10]
        info["files"] = sum(1 for _ in porcelain.iter_tree(store, commit["tree"]))
    return info


def render_repo_list(hub: Hub, repos: list[RepoRef]) -> bytes:
    cards = []
    for ref in repos:
        slug = html.escape(ref.slug)
        m = _repo_meta(hub, ref)
        desc = (f'<p class=muted>{html.escape(m["description"])}</p>'
                if m["description"] else "")
        if m["short"]:
            stat = (f'{m["files"]} files · <span class=hash>{html.escape(m["short"])}'
                    f'</span> · {html.escape(m["date"])}')
        else:
            stat = _("empty")
        cards.append(
            f'<div class="card repo"><h3><a href="/{slug}/">{slug}</a> '
            f'{web.badge(ref.visibility)}</h3>{desc}'
            f'<p class=muted>{stat}</p></div>'
        )
    body = (
        f"<h1>{_('repositories')} "
        f'<span class=muted style="font-weight:400">({len(repos)})</span></h1>'
        f"{''.join(cards) or '<p class=muted>—</p>'}"
    )
    return web._page("wit hub", body, home="/")


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # stil
        pass

    @property
    def _hub(self) -> Hub:
        return self.server.hub  # type: ignore[attr-defined]

    @property
    def _policy(self) -> AccessPolicy:
        return self.server.policy  # type: ignore[attr-defined]

    # -- helpers ----------------------------------------------------------

    def _parts(self) -> tuple[list[str], dict]:
        parsed = urllib.parse.urlparse(self.path)
        parts = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
        return parts, urllib.parse.parse_qs(parsed.query)

    def _principal(self) -> Principal | None:
        auth = self.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.startswith("Bearer ") else None
        return self._policy.principal_for(token)

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

    def _authorize(self, ref: RepoRef, *, write: bool) -> bool:
        """Enforce the access policy; send the right error and return False on deny.

        Reads on a forbidden repo answer 404 (don't leak private repo existence);
        writes answer 401 (no token) or 403 (token without rights)."""
        principal = self._principal()
        if write:
            if self._policy.can_write(principal, ref):
                return True
            self._send_text("unauthorized", 401 if principal is None else 403)
            return False
        if self._policy.can_read(principal, ref):
            return True
        self._send_text("not found", 404)
        return False

    def _drain(self) -> None:
        """Discard the request body, so a rejected upload still gets its response
        (closing mid-stream would give the client a broken pipe, not the error)."""
        remaining = int(self.headers.get("Content-Length", 0))
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, _CHUNK))
            if not chunk:
                break
            remaining -= len(chunk)

    def _authorize_body(self, ref: RepoRef, *, write: bool) -> bool:
        """Like ``_authorize`` but for body-carrying requests: drain first on deny."""
        principal = self._principal()
        ok = (self._policy.can_write if write
              else self._policy.can_read)(principal, ref)
        if ok:
            return True
        self._drain()
        if write:
            self._send_text("unauthorized", 401 if principal is None else 403)
        else:
            self._send_text("not found", 404)
        return False

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
            principal = self._principal()
            visible = [r for r in self._hub.list()
                       if self._policy.can_read(principal, r)]
            self._send_html(render_repo_list(self._hub, visible))
            return
        resolved = self._resolve(parts)
        if resolved is None:
            return
        ref, rest = resolved
        if not self._authorize(ref, write=False):
            return
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
            self._send_html(web.render_index(store, ref.path, base, heading=ref.slug))
        elif rest[0] == "commit" and len(rest) == 2:
            self._send_html(web.render_commit(store, rest[1], base))
        elif rest[0] == "tree" and len(rest) >= 2:
            commit = web.resolve_commit(ref.path, rest[1]) or rest[1]
            page = web.render_tree(store, commit, "/".join(rest[2:]), base)
            self._send_html(page or web._page("404", "not found"),
                            200 if page else 404)
        elif rest[0] == "view" and len(rest) >= 3:
            commit = web.resolve_commit(ref.path, rest[1]) or rest[1]
            page = web.render_blob_view(store, commit, "/".join(rest[2:]), base)
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
        if not self._authorize(ref, write=False):
            return
        if len(rest) == 3 and rest[0] == "objects":
            store = self._hub.store_for(ref)
            ok = rest[1] in KINDS and store.has(rest[1], rest[2])
            self._send_text("", 200 if ok else 404)
        else:
            self._send_text("", 404)

    # -- PUT: object upload ----------------------------------------------

    def do_PUT(self) -> None:
        parts, query = self._parts()
        if len(parts) == 2:  # PUT /<owner>/<name> -> create the repo
            self._create_repo(parts[0], parts[1], query)
            return
        resolved = self._resolve(parts)
        if resolved is None:
            return
        ref, rest = resolved
        if not self._authorize_body(ref, write=True):
            return
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

    def _create_repo(self, owner: str, name: str, query: dict) -> None:
        """Create a hosted repo in the owner's namespace (idempotent).

        Authorizing against a synthetic ``RepoRef`` means a missing repo answers
        the same 401/403 as an existing one a stranger may not touch — existence
        is never leaked. ``FileExistsError`` (caller already passed can_write) is
        a benign re-create, so it reports 200 instead of an error."""
        self._drain()  # a PUT may carry a body; consume it before replying
        visibility = ("public" if query.get("visibility", [""])[0] == "public"
                      else "private")
        synthetic = RepoRef(
            owner, name, self._hub._repo_path(owner, name), visibility)
        principal = self._principal()
        if not self._policy.can_write(principal, synthetic):
            self._send_text("unauthorized", 401 if principal is None else 403)
            return
        try:
            self._hub.create(owner, name, visibility)
        except FileExistsError:
            self._send_text("exists", 200)
        except ValueError:
            self._send_text("invalid owner/name", 400)
        else:
            self._send_text("created", 201)

    # -- POST: ref CAS, batch upload, batch download ----------------------

    def do_POST(self) -> None:
        parts, _q = self._parts()
        resolved = self._resolve(parts)
        if resolved is None:
            return
        ref, rest = resolved
        head = rest[0] if rest else ""
        if head == "fetch":  # batch download is a read
            if self._authorize_body(ref, write=False):
                self._batch_download(ref)
        elif head == "objects":  # batch upload
            if self._authorize_body(ref, write=True):
                self._batch_upload(ref)
        elif head == "refs":
            if self._authorize_body(ref, write=True):
                self._cas_ref(ref, rest)
        else:
            self._send_text("bad request", 400)

    def _cas_ref(self, ref: RepoRef, rest: list[str]) -> None:
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._send_text("bad request", 400)
            return
        ok = self._hub.remote_for(ref).compare_and_swap_ref(
            "/".join(rest), payload.get("expected"), payload["new"])
        body = json.dumps({"ok": bool(ok)}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _batch_upload(self, ref: RepoRef) -> None:
        store = self._hub.store_for(ref)
        length = int(self.headers.get("Content-Length", 0))
        for kind, oid, data in wire.read_frames(self.rfile, length):
            if kind not in KINDS:
                self._send_text("bad request", 400)
                return
            stored = store.put(kind, data)  # re-hashes -> verifies in transit
            if stored != oid:
                store._path(kind, stored).unlink(missing_ok=True)
                self._send_text("hash mismatch", 400)
                return
        self._send_text("", 201)

    def _batch_download(self, ref: RepoRef) -> None:
        store = self._hub.store_for(ref)
        length = int(self.headers.get("Content-Length", 0))
        try:
            want = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._send_text("bad request", 400)
            return
        present = [
            (kind, oid, store.path_for(kind, oid).stat().st_size)
            for kind, oid in want
            if kind in KINDS and store.has(kind, oid)
        ]
        total = sum(wire.frame_size(k, o, sz) for k, o, sz in present)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(total))
        self.end_headers()
        for kind, oid, sz in present:
            wire.stream_object(
                self.wfile, store.path_for(kind, oid), kind, oid, sz)


def make_server(
    root: Path, host: str = "127.0.0.1", port: int = 8080
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), _Handler)
    server.hub = Hub(root)  # type: ignore[attr-defined]
    server.policy = AccessPolicy.load(root)  # type: ignore[attr-defined]
    return server


def serve(root: Path, host: str = "127.0.0.1", port: int = 8080) -> None:
    server = make_server(root, host, port)
    print(_("wit hub at http://{host}:{port}  (Ctrl-C to stop)").format(
        host=host, port=port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
