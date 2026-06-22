"""Read-only web interface: browse commits, trees and files online.

Server-side rendered with the stdlib (``http.server``), no dependencies, no JS. Files
are served streaming from the object store (memory efficient, even for large
documents). Intentionally read-only: there are no write endpoints.

Routes (optionally under a per-repo ``base`` prefix, used by the hub):
``/`` index · ``/commit/<id>`` · ``/tree/<id>/<path>`` directory ·
``/view/<id>/<path>`` an HTML file preview · ``/blob/<id>/<path>`` the raw bytes
(used by the preview for images/PDF and for downloads).

The resolution helpers (`resolve_commit`, `tree_listing`, `blob_entry`) are pure
functions, decoupled from HTTP, so they can be tested directly.
"""

from __future__ import annotations

import html
import mimetypes
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .commits import log, read_commit
from .i18n import _
from .objects import ObjectStore
from .refs import read_head
from .trees import read_tree

_CHUNK = 1024 * 1024
_MAX_INLINE = 512 * 1024  # only inline text/markdown up to this size

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico"}
_TEXT_EXT = {
    ".txt", ".md", ".markdown", ".org", ".rst", ".csv", ".tsv", ".log",
    ".json", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".py", ".js", ".ts", ".html", ".css", ".sh", ".c", ".h", ".cpp", ".rs",
    ".go", ".java", ".sql", ".tex", ".bib",
}
_MD_EXT = {".md", ".markdown"}


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
    """Sorted (name, entry) list of a directory inside a commit (alphabetical)."""
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


# -- presentation helpers ----------------------------------------------------

def humansize(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def badge(visibility: str) -> str:
    cls = "public" if visibility == "public" else "private"
    return f'<span class="badge {cls}">{html.escape(visibility)}</span>'


def _icon(name: str, is_dir: bool) -> str:
    if is_dir:
        return "📁"
    ext = Path(name).suffix.lower()
    if ext in _IMAGE_EXT:
        return "🖼"
    if ext == ".pdf":
        return "📕"
    if ext in _TEXT_EXT:
        return "📄"
    return "📦"


def _inline_md(s: str) -> str:
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)

    def link(m: re.Match) -> str:
        text, url = m.group(1), m.group(2)
        if not re.match(r"(https?:|mailto:|/|#|\w[\w./-]*$)", url, re.I):
            return m.group(0)  # block javascript: and the like
        return f'<a href="{url}">{text}</a>'

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, s)


def render_markdown(text: str) -> str:
    """A tiny, safe Markdown subset → HTML (input is escaped first)."""
    out: list[str] = []
    in_code = in_list = False
    for line in html.escape(text).split("\n"):
        if line.strip().startswith("```"):
            out.append("</code></pre>" if in_code else "<pre><code>")
            in_code = not in_code
            continue
        if in_code:
            out.append(line)
            continue
        heading = re.match(r"(#{1,6})\s+(.*)", line)
        item = re.match(r"\s*[-*]\s+(.*)", line)
        if heading:
            if in_list:
                out.append("</ul>"); in_list = False
            lvl = len(heading.group(1))
            out.append(f"<h{lvl}>{_inline_md(heading.group(2))}</h{lvl}>")
        elif item:
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{_inline_md(item.group(1))}</li>")
        else:
            if in_list:
                out.append("</ul>"); in_list = False
            if line.strip():
                out.append(f"<p>{_inline_md(line)}</p>")
    if in_list:
        out.append("</ul>")
    if in_code:
        out.append("</code></pre>")
    return "\n".join(out)


# -- HTML shell --------------------------------------------------------------

_STYLE = """
:root{--accent:#3b5bdb;--bg:#fff;--fg:#1a1a1a;--muted:#6b7280;--card:#f7f7f9;--border:#e5e7eb}
@media(prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e6e6e6;--muted:#9aa0aa;--card:#171a21;--border:#262b36;--accent:#7aa2ff}}
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--fg);line-height:1.5}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.topbar{display:flex;align-items:center;gap:1rem;padding:.7rem 1.2rem;border-bottom:1px solid var(--border);position:sticky;top:0;background:var(--bg)}
.brand{font-weight:700;font-size:1.1rem;color:var(--fg)}
.container{max-width:62rem;margin:0 auto;padding:1.4rem 1.2rem}
.muted{color:var(--muted);font-size:.9em}
.badge{display:inline-block;font-size:.72rem;padding:.05rem .5rem;border-radius:999px;border:1px solid var(--border);color:var(--muted);vertical-align:middle}
.badge.public{color:#16794c;border-color:#16794c66}
.badge.private{color:#9a3412;border-color:#9a341266}
code,pre,.hash{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
code{background:var(--card);padding:.1rem .35rem;border-radius:4px;font-size:.9em}
pre{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:1rem;overflow:auto}
.hash{color:var(--muted)}
table.tree{width:100%;border-collapse:collapse;margin-top:.5rem}
table.tree td{padding:.35rem .5rem;border-bottom:1px solid var(--border)}
table.tree td.size{text-align:right;color:var(--muted);white-space:nowrap}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:1rem 1.2rem;margin:.7rem 0}
.repo h3{margin:.1rem 0}
.crumbs{margin:.2rem 0 1rem;color:var(--muted)}
.commit{display:flex;gap:.7rem;align-items:baseline;padding:.45rem 0;border-bottom:1px solid var(--border)}
.preview-img{max-width:100%;border:1px solid var(--border);border-radius:8px}
.pdfview{width:100%;height:80vh;border:1px solid var(--border);border-radius:8px}
.btn{display:inline-block;padding:.3rem .8rem;border:1px solid var(--border);border-radius:8px;color:var(--fg);background:var(--card)}
.btn:hover{text-decoration:none;border-color:var(--accent)}
h1,h2,h3{line-height:1.25}
"""


def _page(title: str, body: str, *, home: str = "/") -> bytes:
    return (
        "<!doctype html><html lang=nl><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style>"
        f'<header class=topbar><a class=brand href="{html.escape(home)}">📄 wit</a></header>'
        f'<main class=container>{body}</main>'
    ).encode("utf-8")


def _breadcrumbs(commit_id: str, subpath: str, base: str = "") -> str:
    parts = [c for c in subpath.split("/") if c]
    crumbs = [f'<a href="{base}/tree/{html.escape(commit_id)}/">/</a>']
    acc = ""
    for part in parts:
        acc = f"{acc}{part}/"
        crumbs.append(
            f'<a href="{base}/tree/{html.escape(commit_id)}/{html.escape(acc)}">'
            f'{html.escape(part)}</a>')
    return '<span class=crumbs>' + " / ".join(crumbs) + "</span>"


def _readme_html(store: ObjectStore, head: str) -> str:
    for name, entry in (tree_listing(store, head, "") or []):
        if entry["type"] == "blob" and name.lower() in (
            "readme.md", "readme.markdown", "readme.txt", "readme"
        ):
            data = store.get("blobs", entry["hash"])
            if len(data) <= _MAX_INLINE:
                rendered = render_markdown(data.decode("utf-8", "replace"))
                return f'<div class=card>{rendered}</div>'
    return ""


def render_index(
    store: ObjectStore, wit: Path, base: str = "", heading: str = "wit"
) -> bytes:
    head = read_head(wit)
    title = html.escape(heading)
    if head is None:
        return _page(heading, f"<h1>{title}</h1><p class=muted>{_('no commits yet')}</p>",
                     home=base or "/")
    rows = []
    for cid, commit in log(store, head):
        rows.append(
            f'<div class=commit><a class=hash href="{base}/commit/{html.escape(cid)}">'
            f'{html.escape(cid[3:11])}</a><span>{html.escape(commit["message"])}</span>'
            f'<span class="muted" style="margin-left:auto">{html.escape(commit["time"][:10])}</span></div>'
        )
    body = (
        f"<h1>{title}</h1>"
        f'<p><a class=btn href="{base}/tree/HEAD/">📂 {_("browse HEAD")}</a></p>'
        f"{_readme_html(store, head)}"
        f"<h2>{_('commits')}</h2>{''.join(rows)}"
    )
    return _page(heading, body, home=base or "/")


def render_tree(
    store: ObjectStore, commit_id: str, subpath: str, base: str = ""
) -> bytes | None:
    listing = tree_listing(store, commit_id, subpath)
    if listing is None:
        return None
    listing = sorted(listing, key=lambda kv: (kv[1]["type"] != "tree", kv[0].lower()))
    rows = []
    for name, entry in listing:
        href_path = f"{subpath}/{name}" if subpath else name
        is_dir = entry["type"] == "tree"
        verb = "tree" if is_dir else "view"
        label = html.escape(name) + ("/" if is_dir else "")
        size = "" if is_dir else humansize(entry.get("size", 0))
        rows.append(
            f'<tr><td>{_icon(name, is_dir)} '
            f'<a href="{base}/{verb}/{html.escape(commit_id)}/{html.escape(href_path)}">'
            f'{label}</a></td><td class=size>{size}</td></tr>'
        )
    body = (
        f"{_breadcrumbs(commit_id, subpath, base)}"
        f"<table class=tree>{''.join(rows)}</table>"
    )
    return _page(f"{subpath or '/'}", body, home=base or "/")


def render_commit(store: ObjectStore, commit_id: str, base: str = "") -> bytes:
    commit = read_commit(store, commit_id)
    parents = " ".join(
        f'<a class=hash href="{base}/commit/{html.escape(p)}">{html.escape(p[3:11])}</a>'
        for p in commit["parents"]
    )
    body = (
        f'<h2 class=hash>{html.escape(commit_id[3:11])}</h2>'
        f'<div class=card><p>{html.escape(commit["message"])}</p>'
        f'<p class=muted>{html.escape(commit["time"])} · {html.escape(commit["host"])}</p>'
        f'<p class=muted>parents: {parents or "—"}</p></div>'
        f'<p><a class=btn href="{base}/tree/{html.escape(commit_id)}/">📂 {_("browse this commit")}</a></p>'
    )
    return _page(f"commit {commit_id[3:11]}", body, home=base or "/")


def render_blob_view(
    store: ObjectStore, commit_id: str, path: str, base: str = ""
) -> bytes | None:
    entry = blob_entry(store, commit_id, path)
    if entry is None:
        return None
    name = path.rsplit("/", 1)[-1]
    ext = Path(name).suffix.lower()
    parent = "/".join(path.split("/")[:-1])
    raw = f"{base}/blob/{html.escape(commit_id)}/{html.escape(path)}"
    size = entry.get("size", 0)

    if ext in _IMAGE_EXT:
        preview = f'<img class=preview-img src="{raw}" alt="{html.escape(name)}">'
    elif ext == ".pdf":
        preview = f'<embed class=pdfview src="{raw}" type="application/pdf">'
    elif ext in _TEXT_EXT and size <= _MAX_INLINE:
        text = store.get("blobs", entry["hash"]).decode("utf-8", "replace")
        if ext in _MD_EXT:
            preview = f"<div class=card>{render_markdown(text)}</div>"
        else:
            preview = f"<pre>{html.escape(text)}</pre>"
    else:
        preview = f'<p class=muted>{_("binary or large file — use download")}</p>'

    header = (
        f"{_breadcrumbs(commit_id, parent, base)}"
        f'<h2>{_icon(name, False)} {html.escape(name)} '
        f'<span class=muted style="font-weight:400">{humansize(size)}</span></h2>'
        f'<p><a class=btn href="{raw}?download=1">⬇ {_("download")}</a></p>'
    )
    return _page(name, header + preview, home=base or "/")


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

    def _not_found(self) -> None:
        self._send_html(_page("404", f"<h1>404</h1><p>{_('not found')}</p>"), 404)

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
                self._send_html(page) if page else self._not_found()
            elif parts[0] == "view" and len(parts) >= 3:
                commit = resolve_commit(self._wit, parts[1]) or parts[1]
                page = render_blob_view(self._store, commit, "/".join(parts[2:]))
                self._send_html(page) if page else self._not_found()
            elif parts[0] == "blob" and len(parts) >= 3:
                commit = resolve_commit(self._wit, parts[1]) or parts[1]
                self._serve_blob(commit, "/".join(parts[2:]), "download" in query)
            else:
                self._not_found()
        except (KeyError, ValueError):
            self._not_found()

    def _serve_blob(self, commit_id: str, path: str, download: bool) -> None:
        entry = blob_entry(self._store, commit_id, path)
        if entry is None:
            self._not_found()
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
