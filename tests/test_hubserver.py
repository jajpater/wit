"""End-to-end: clone/push/pull over HTTP against a running hub server.

Spins up the real ThreadingHTTPServer on an ephemeral port and drives it through
``HttpRemote`` + ``sync`` — the same path ``wit clone https://…`` takes.
"""

import os
import threading

import pytest

from wit import porcelain, sync
from wit.hub import Hub
from wit.hubserver import make_server
from wit.objects import ObjectStore
from wit.refs import read_head
from wit.remote import make_remote
from wit.repo import init

_T = "2026-01-01T00:00:00.000000Z"


@pytest.fixture
def hub_url(tmp_path):
    """A running hub with one empty repo ``alice/library``; yields its URL."""
    Hub.init(tmp_path / "srv").create("alice", "library", visibility="public")
    server = make_server(tmp_path / "srv", host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}/alice/library"
    finally:
        server.shutdown()
        thread.join()


def _seed(path):
    wit = init(path)
    store = ObjectStore(wit)
    files = {
        "a.txt": b"hallo\n",
        "sub/img.bin": os.urandom(2 * 1024 * 1024 + 5),
        "sub/leeg.dat": b"",
    }
    for rel, data in files.items():
        p = path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    porcelain.add(wit, store, [str(path)])
    head = porcelain.commit(wit, store, "init", time=_T)
    return wit, store, head, files


def test_make_remote_builds_http_remote(hub_url):
    from wit.http_remote import HttpRemote
    assert isinstance(make_remote(hub_url), HttpRemote)


def test_push_then_clone_is_byte_identical(tmp_path, hub_url):
    src = tmp_path / "src"
    src.mkdir()
    wit, store, head, files = _seed(src)

    pushed = sync.push(wit, store, make_remote(hub_url))
    assert pushed == head

    dest = tmp_path / "clone"
    cloned = sync.clone(make_remote(hub_url), dest)
    assert read_head(cloned) == head
    for rel, data in files.items():
        assert (dest / rel).read_bytes() == data


def test_pull_fast_forwards_a_new_commit(tmp_path, hub_url):
    # producer pushes an initial commit
    src = tmp_path / "src"
    src.mkdir()
    wit, store, _head, _files = _seed(src)
    sync.push(wit, store, make_remote(hub_url))

    # consumer clones, then producer pushes a second commit
    dest = tmp_path / "clone"
    dwit = sync.clone(make_remote(hub_url), dest)
    dstore = ObjectStore(dwit)

    (src / "b.txt").write_bytes(b"tweede\n")
    porcelain.add(wit, store, [str(src / "b.txt")])
    head2 = porcelain.commit(wit, store, "second", time=_T)
    sync.push(wit, store, make_remote(hub_url))

    result = sync.pull(dwit, dstore, make_remote(hub_url))
    assert result == (head2, [])
    assert (dest / "b.txt").read_bytes() == b"tweede\n"


def test_push_is_rejected_when_remote_moved(tmp_path, hub_url):
    # two clones of the same base; both commit; second push must be rejected
    src = tmp_path / "src"
    src.mkdir()
    wit, store, _head, _files = _seed(src)
    sync.push(wit, store, make_remote(hub_url))

    a = sync.clone(make_remote(hub_url), tmp_path / "a")
    b = sync.clone(make_remote(hub_url), tmp_path / "b")
    astore, bstore = ObjectStore(a), ObjectStore(b)

    (a / "x.txt").write_bytes(b"x\n")
    porcelain.add(a, astore, [str(a / "x.txt")])
    porcelain.commit(a, astore, "from a", time=_T)
    sync.push(a, astore, make_remote(hub_url))

    (b / "y.txt").write_bytes(b"y\n")
    porcelain.add(b, bstore, [str(b / "y.txt")])
    porcelain.commit(b, bstore, "from b", time=_T)
    with pytest.raises(ValueError):
        sync.push(b, bstore, make_remote(hub_url))
