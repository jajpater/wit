"""Fase 2: read-only webinterface — bladeren + byte-identieke download."""

import os
import threading
import urllib.error
import urllib.request

from wit import porcelain
from wit.objects import ObjectStore
from wit.refs import read_head
from wit.repo import init
from wit.web import blob_entry, make_server, render_tree, tree_listing


def _setup(tmp_path):
    wit = init(tmp_path)
    store = ObjectStore(wit)
    (tmp_path / "calvijn.md").write_bytes(b"Calvijn over het verbond.\n")
    (tmp_path / "scans").mkdir()
    blob = os.urandom(300 * 1024 + 7)
    (tmp_path / "scans" / "doc.bin").write_bytes(blob)
    porcelain.add(wit, store, [str(tmp_path)])
    porcelain.commit(wit, store, "init", time="2026-01-01T00:00:00.000000Z")
    return tmp_path, wit, store, blob


def test_tree_listing_and_blob_entry(tmp_path):
    _, wit, store, _ = _setup(tmp_path)
    head = read_head(wit)
    names = [n for n, _ in tree_listing(store, head, "")]
    assert names == ["calvijn.md", "scans"]
    sub = dict(tree_listing(store, head, "scans"))
    assert "doc.bin" in sub
    assert blob_entry(store, head, "scans/doc.bin")["type"] == "blob"
    assert blob_entry(store, head, "scans") is None        # map is geen blob
    assert tree_listing(store, head, "bestaat-niet") is None


def test_render_tree_contains_links(tmp_path):
    _, wit, store, _ = _setup(tmp_path)
    page = render_tree(store, read_head(wit), "").decode()
    assert "calvijn.md" in page
    assert "/tree/" in page and "scans/" in page


def test_live_server_serves_byte_identical_blob(tmp_path):
    root, wit, store, blob = _setup(tmp_path)
    server = make_server(wit, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        # overzicht laadt
        assert b"commits" in urllib.request.urlopen(f"{base}/").read()
        # blob byte-identiek via HEAD
        got = urllib.request.urlopen(f"{base}/blob/HEAD/scans/doc.bin").read()
        assert got == blob
        # download forceert attachment-header
        with urllib.request.urlopen(f"{base}/blob/HEAD/calvijn.md?download=1") as resp:
            assert "attachment" in resp.headers.get("Content-Disposition", "")
        # 404 voor onbekend pad
        try:
            urllib.request.urlopen(f"{base}/blob/HEAD/weg.txt")
            assert False, "verwachtte 404"
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
        thread.join()
