"""Access policy logic and ``wit-hub`` CLI."""

import pytest

from wit import hubcli
from wit.access import AccessPolicy, add_token, load_tokens
from wit.hub import Hub, RepoRef


def _repo(owner, name, visibility):
    return RepoRef(owner, name, path=None, visibility=visibility)


def test_token_mode_read_and_write(tmp_path):
    add_token(tmp_path, "alice", token="t-alice")
    add_token(tmp_path, "bob", token="t-bob")
    policy = AccessPolicy.load(tmp_path)  # no hub.toml -> default "token"

    pub = _repo("alice", "library", "public")
    priv = _repo("alice", "secret", "private")
    alice = policy.principal_for("t-alice")
    bob = policy.principal_for("t-bob")

    # public: anyone reads, only owner writes
    assert policy.can_read(None, pub)
    assert policy.can_write(alice, pub)
    assert not policy.can_write(bob, pub)
    assert not policy.can_write(None, pub)

    # private: only owner reads or writes
    assert not policy.can_read(None, priv)
    assert not policy.can_read(bob, priv)
    assert policy.can_read(alice, priv)
    assert policy.can_write(alice, priv)


def test_open_mode_allows_everything(tmp_path):
    (tmp_path / "hub.toml").write_text('auth_mode = "open"\n')
    policy = AccessPolicy.load(tmp_path)
    priv = _repo("alice", "secret", "private")
    assert policy.can_read(None, priv)
    assert policy.can_write(None, priv)


def test_unknown_token_is_anonymous(tmp_path):
    add_token(tmp_path, "alice", token="t-alice")
    policy = AccessPolicy.load(tmp_path)
    assert policy.principal_for("nope") is None
    assert policy.principal_for(None) is None


def test_add_token_persists_multiple(tmp_path):
    add_token(tmp_path, "alice", token="t1")
    add_token(tmp_path, "bob", token="t2")
    assert load_tokens(tmp_path) == {"t1": "alice", "t2": "bob"}


# -- CLI ------------------------------------------------------------------

def test_cli_init_create_list(tmp_path, capsys):
    root = str(tmp_path / "srv")
    assert hubcli.main(["--root", root, "init"]) == 0
    assert hubcli.main(["--root", root, "create", "alice/library", "--public"]) == 0
    assert hubcli.main(["--root", root, "create", "bob/thesis"]) == 0
    capsys.readouterr()
    assert hubcli.main(["--root", root, "list"]) == 0
    out = capsys.readouterr().out
    assert "alice/library  (public)" in out
    assert "bob/thesis  (private)" in out


def test_cli_create_rejects_bad_slug(tmp_path):
    root = str(tmp_path / "srv")
    hubcli.main(["--root", root, "init"])
    with pytest.raises(SystemExit):
        hubcli.main(["--root", root, "create", "no-slash"])


def test_cli_token_add_and_list(tmp_path, capsys):
    root = str(tmp_path / "srv")
    hubcli.main(["--root", root, "init"])
    hubcli.main(["--root", root, "token", "add", "alice", "--token", "secret123"])
    capsys.readouterr()
    hubcli.main(["--root", root, "token", "list"])
    out = capsys.readouterr().out
    assert "alice" in out
    assert load_tokens(tmp_path / "srv") == {"secret123": "alice"}


def test_cli_rm(tmp_path):
    root = str(tmp_path / "srv")
    hubcli.main(["--root", root, "init"])
    hubcli.main(["--root", root, "create", "alice/library"])
    assert hubcli.main(["--root", root, "rm", "alice/library"]) == 0
    assert Hub(tmp_path / "srv").resolve("alice", "library") is None


def test_cli_visibility(tmp_path):
    root = str(tmp_path / "srv")
    hubcli.main(["--root", root, "init"])
    hubcli.main(["--root", root, "create", "alice/library"])  # private
    assert hubcli.main(
        ["--root", root, "visibility", "alice/library", "public"]) == 0
    assert Hub(tmp_path / "srv").resolve("alice", "library").visibility == "public"
