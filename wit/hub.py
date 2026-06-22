"""The multi-repo host layer ("hub").

A hub hosts many independent ``wit`` repositories under one root, the way GitHub
hosts many git repositories. See ARCHITECTURE-hub.md for the full design.

The guiding constraint: the ``wit`` core stays single-repo. The hub only adds a
registry (which repos exist), a router (mount the right store per ``owner/name``),
and access policy — without touching the content-addressed truth. Each hosted repo
is an ordinary bare repository, reached through the existing ``WitServerRemote``
(atomic ref-CAS via ``flock``) for transport and ``ObjectStore`` for the viewer.

This module is a skeleton: the directory truth and repo lifecycle are real; the
registry cache (``registry.sqlite``), HTTP router, and access policy are sketched as
TODOs against the design doc.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import repo
from .i18n import _
from .objects import ObjectStore
from .remote import WitServerRemote

#: Suffix of a hosted repository directory: ``repos/<owner>/<name>.wit``.
REPO_SUFFIX = ".wit"

VISIBILITIES = ("public", "private")


@dataclass(frozen=True)
class RepoRef:
    """A hosted repository, identified by ``owner/name``."""

    owner: str
    name: str
    path: Path  # .../repos/<owner>/<name>.wit
    visibility: str = "private"

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"


def _valid_segment(seg: str) -> bool:
    """A single owner/name segment: no separators, dotfiles, or traversal."""
    return bool(seg) and "/" not in seg and seg not in (".", "..") \
        and not seg.startswith(".")


class Hub:
    """Hosts many repositories under ``root/repos/<owner>/<name>.wit``.

    The directory structure is the truth (which repos exist); ``registry.sqlite``
    is a rebuildable cache and not yet implemented here — ``list`` and ``resolve``
    scan the directory directly.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.repos_dir = self.root / "repos"

    # -- lifecycle --------------------------------------------------------

    @classmethod
    def init(cls, root: Path) -> "Hub":
        """Lay out an empty hub at ``root`` and return it."""
        hub = cls(root)
        hub.repos_dir.mkdir(parents=True, exist_ok=True)
        config = hub.root / "hub.toml"
        if not config.exists():
            config.write_text(
                'default_visibility = "private"\n'
                'host = "127.0.0.1"\n'
                "port = 8080\n"
            )
        return hub

    def _repo_path(self, owner: str, name: str) -> Path:
        return self.repos_dir / owner / (name + REPO_SUFFIX)

    def create(
        self, owner: str, name: str, visibility: str = "private"
    ) -> RepoRef:
        """Create an empty hosted repository and return its ref."""
        if not (_valid_segment(owner) and _valid_segment(name)):
            raise ValueError(_("invalid owner/name: {slug}").format(
                slug=f"{owner}/{name}"))
        if visibility not in VISIBILITIES:
            raise ValueError(_("invalid visibility: {v}").format(v=visibility))
        path = self._repo_path(owner, name)
        if path.exists():
            raise FileExistsError(_("{slug} already exists").format(
                slug=f"{owner}/{name}"))
        repo.init_at(path)
        (path / "repo.toml").write_text(
            f'owner = "{owner}"\n'
            f'name = "{name}"\n'
            f'visibility = "{visibility}"\n'
            'description = ""\n'
        )
        return RepoRef(owner, name, path, visibility)

    def delete(self, owner: str, name: str) -> None:
        import shutil

        path = self._repo_path(owner, name)
        if not path.is_dir():
            raise FileNotFoundError(_("no such repo: {slug}").format(
                slug=f"{owner}/{name}"))
        shutil.rmtree(path)

    # -- registry (scan-based; registry.sqlite cache is a TODO) -----------

    def resolve(self, owner: str, name: str) -> RepoRef | None:
        path = self._repo_path(owner, name)
        if not path.is_dir():
            return None
        return RepoRef(owner, name, path, self._visibility(path))

    def list(self) -> list[RepoRef]:
        """All hosted repositories, sorted by slug.

        Access filtering (hide ``private`` repos from anonymous viewers) belongs
        in the router, not here — see ARCHITECTURE-hub.md, "Access policy".
        """
        refs: list[RepoRef] = []
        if not self.repos_dir.is_dir():
            return refs
        for owner_dir in sorted(self.repos_dir.iterdir()):
            if not owner_dir.is_dir():
                continue
            for repo_dir in sorted(owner_dir.glob(f"*{REPO_SUFFIX}")):
                if not repo_dir.is_dir():
                    continue
                name = repo_dir.name[: -len(REPO_SUFFIX)]
                refs.append(RepoRef(
                    owner_dir.name, name, repo_dir,
                    self._visibility(repo_dir)))
        return refs

    def rescan(self) -> None:
        """Rebuild the registry cache from the directory truth. TODO: persist
        to ``registry.sqlite``; for now ``list``/``resolve`` scan live."""

    @staticmethod
    def _visibility(repo_path: Path) -> str:
        meta = repo_path / "repo.toml"
        if not meta.exists():
            return "private"
        try:
            return tomllib.loads(meta.read_text()).get("visibility", "private")
        except (OSError, tomllib.TOMLDecodeError):
            return "private"

    # -- bridges to the existing core (no new storage logic) --------------

    def remote_for(self, ref: RepoRef) -> WitServerRemote:
        """The smart remote (atomic ref-CAS + gc) for transport."""
        return WitServerRemote(ref.path)

    def store_for(self, ref: RepoRef) -> ObjectStore:
        """The object store for the read-only viewer."""
        return ObjectStore(ref.path)
