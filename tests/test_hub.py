"""The multi-repo host layer: repo lifecycle, registry scan, and routing.

Skeleton-level coverage — the HTTP router and registry cache are still TODO
(see ARCHITECTURE-hub.md); this pins down the directory truth and the bridges to
the existing core.
"""

import pytest

from wit.hub import Hub, RepoRef
from wit.objects import ObjectStore
from wit.remote import WitServerRemote


def test_create_lays_out_a_bare_repo(tmp_path):
    hub = Hub.init(tmp_path / "srv")
    ref = hub.create("alice", "library", visibility="public")
    assert ref == RepoRef("alice", "library", ref.path, "public")
    # bare layout: objects/refs live directly under the repo dir, no .wit
    assert (ref.path / "objects" / "blobs").is_dir()
    assert (ref.path / "refs" / "heads").is_dir()
    assert (ref.path / "repo.toml").exists()


def test_list_and_resolve_scan_the_directory(tmp_path):
    hub = Hub.init(tmp_path / "srv")
    hub.create("alice", "library", visibility="public")
    hub.create("bob", "thesis")

    slugs = [(r.slug, r.visibility) for r in hub.list()]
    assert slugs == [("alice/library", "public"), ("bob/thesis", "private")]

    assert hub.resolve("alice", "library").visibility == "public"
    assert hub.resolve("nobody", "nope") is None


def test_bridges_to_existing_core(tmp_path):
    hub = Hub.init(tmp_path / "srv")
    ref = hub.create("alice", "library")
    assert isinstance(hub.remote_for(ref), WitServerRemote)
    assert isinstance(hub.store_for(ref), ObjectStore)


def test_duplicate_is_rejected(tmp_path):
    hub = Hub.init(tmp_path / "srv")
    hub.create("alice", "library")
    with pytest.raises(FileExistsError):
        hub.create("alice", "library")


def test_set_visibility(tmp_path):
    hub = Hub.init(tmp_path / "srv")
    hub.create("alice", "library")  # private by default
    assert hub.resolve("alice", "library").visibility == "private"

    ref = hub.set_visibility("alice", "library", "public")
    assert ref.visibility == "public"
    assert hub.resolve("alice", "library").visibility == "public"  # persisted

    with pytest.raises(ValueError):
        hub.set_visibility("alice", "library", "secret")
    with pytest.raises(FileNotFoundError):
        hub.set_visibility("alice", "nope", "public")


@pytest.mark.parametrize("owner,name", [
    ("..", "x"), ("a/b", "x"), ("alice", ".."), ("", "x"), (".hidden", "x"),
])
def test_path_traversal_is_rejected(tmp_path, owner, name):
    hub = Hub.init(tmp_path / "srv")
    with pytest.raises(ValueError):
        hub.create(owner, name)


def test_delete_removes_the_repo(tmp_path):
    hub = Hub.init(tmp_path / "srv")
    hub.create("alice", "library")
    hub.delete("alice", "library")
    assert hub.resolve("alice", "library") is None
    with pytest.raises(FileNotFoundError):
        hub.delete("alice", "library")
