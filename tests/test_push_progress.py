"""Push progress: enabled by default, explicit overrides and interrupted streaming."""

import sys
from contextlib import nullcontext

import pytest

from wit import cli, porcelain
from wit.http_remote import HttpRemote
from wit.objects import ObjectStore
from wit.remote import FilesystemRemote
from wit.repo import init


@pytest.mark.parametrize(
    "terminal, flags, visible",
    [
        (True, [], True),
        (False, [], True),
        (True, ["--progress"], True),
        (False, ["--progress"], True),
        (True, ["--no-progress"], False),
        (False, ["--no-progress"], False),
    ],
)
def test_cli_push_progress(tmp_path, monkeypatch, capsys, terminal, flags, visible):
    src = tmp_path / "src"
    src.mkdir()
    wit = init(src)
    store = ObjectStore(wit)
    (src / "a.txt").write_text("hello")
    porcelain.add(wit, store, [str(src)])
    head = porcelain.commit(wit, store, "initial")
    monkeypatch.chdir(src)
    monkeypatch.setattr(sys.stderr, "isatty", lambda: terminal)

    assert cli.main(["push", str(tmp_path / "remote"), *flags]) == 0
    output = capsys.readouterr()
    assert "pushed to" in output.out
    if visible:
        assert "100%" in output.err
        assert "3/3 objects" in output.err
        assert output.err.endswith("\n")
    else:
        assert output.err == ""
    remote = FilesystemRemote(tmp_path / "remote")
    assert remote.read_ref("refs/heads/main") == head

    # A push that is already up to date has no transfer to display.
    assert cli.main(["push", str(tmp_path / "remote"), *flags]) == 0
    assert capsys.readouterr().err == ""


def test_filesystem_progress_counts_existing_objects(tmp_path, capsys):
    store = ObjectStore(init(tmp_path / "src"))
    oid = store.put("blobs", b"already uploaded")
    remote = FilesystemRemote(tmp_path / "remote")
    remote.upload(store, "blobs", oid)
    remote.upload_objects(store, iter([("blobs", oid)]), progress=True)
    assert "100%  1/1 objects" in capsys.readouterr().err
    assert remote.store.get("blobs", oid) == b"already uploaded"


def test_http_progress_during_large_object(tmp_path, monkeypatch, capsys):
    store = ObjectStore(init(tmp_path / "src"))
    data = b"x" * (3 * 1024 * 1024)
    oid = store.put("blobs", data)
    chunks = []
    displays = []

    # Force a redraw after every chunk without slowing down the test.
    ticks = iter(range(100))
    monkeypatch.setattr("wit.progress.time.monotonic", lambda: next(ticks))

    def receive(req):
        for chunk in req.data:
            chunks.append(chunk)
            displays.append(capsys.readouterr().err)
        displays.append(capsys.readouterr().err)
        assert sum(map(len, chunks)) == int(req.get_header("Content-length"))
        return nullcontext()

    monkeypatch.setattr("wit.http_remote.urllib.request.urlopen", receive)
    HttpRemote("http://localhost/alice/library").upload_objects(
        store, [("blobs", oid)], progress=True)
    assert b"".join(chunks).endswith(data)
    assert any("33%" in display for display in displays)
    assert any("66%" in display for display in displays)
    assert "100%" in displays[-1]
    assert capsys.readouterr().err == "\n"


def test_failed_http_upload_ends_progress_line(tmp_path, monkeypatch, capsys):
    store = ObjectStore(init(tmp_path / "src"))
    oid = store.put("blobs", b"x" * (2 * 1024 * 1024))

    def interrupted(req):
        next(req.data)  # header
        next(req.data)  # first chunk; another chunk remains
        raise OSError("connection lost")

    monkeypatch.setattr("wit.http_remote.urllib.request.urlopen", interrupted)
    with pytest.raises(OSError, match="connection lost"):
        HttpRemote("http://localhost/alice/library").upload_objects(
            store, [("blobs", oid)], progress=True)
    output = capsys.readouterr()
    assert "100%" not in output.err
    assert output.err.endswith("\n")
    assert output.out == ""
