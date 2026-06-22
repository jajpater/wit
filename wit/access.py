"""Access policy for the hub — deliberately outside the wit core.

Policy lives here and in the router (`hubserver`), never in `objects.py` / `refs.py`:
leaking it into the content-addressed store would corrupt the truth. See
ARCHITECTURE-hub.md, "Access policy".

Two modes, selected by ``auth_mode`` in ``hub.toml``:

* ``"token"`` (default, secure): a bearer token maps to a principal (an owner
  identity). Reads of ``public`` repos are anonymous; reads of ``private`` repos
  and *all* writes require a token whose owner matches the repo owner.
* ``"open"``: no authentication — every request may read and write everything.
  For a trusted LAN or when a reverse proxy in front does the auth.

Tokens live in ``<root>/tokens.toml`` as ``<token> = { owner = "<name>" }``.
"""

from __future__ import annotations

import secrets
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .hub import RepoRef

TOKENS_FILE = "tokens.toml"
DEFAULT_MODE = "token"
MODES = ("token", "open")


@dataclass(frozen=True)
class Principal:
    """An authenticated identity (an owner namespace)."""

    name: str


def _tokens_path(root: Path) -> Path:
    return Path(root) / TOKENS_FILE


def load_tokens(root: Path) -> dict[str, str]:
    """Map of ``token -> owner`` from ``tokens.toml`` (empty if absent)."""
    path = _tokens_path(root)
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text())
    return {tok: entry["owner"] for tok, entry in data.items()}


def add_token(root: Path, owner: str, token: str | None = None) -> str:
    """Create (or register) a token for ``owner`` and return it."""
    token = token or secrets.token_urlsafe(24)
    path = _tokens_path(root)
    # Append-style write; tomllib has no dumper, so we format by hand. Tokens are
    # url-safe and owners are validated upstream, so quoting is safe.
    existing = path.read_text() if path.exists() else ""
    block = f'["{token}"]\nowner = "{owner}"\n'
    path.write_text(existing + ("\n" if existing and not existing.endswith("\n") else "") + block)
    return token


class AccessPolicy:
    """Answers can-read / can-write for a principal against a repo."""

    def __init__(self, mode: str, tokens: dict[str, str]) -> None:
        self.mode = mode if mode in MODES else DEFAULT_MODE
        self._tokens = tokens

    @classmethod
    def load(cls, root: Path) -> "AccessPolicy":
        mode = DEFAULT_MODE
        cfg = Path(root) / "hub.toml"
        if cfg.exists():
            mode = tomllib.loads(cfg.read_text()).get("auth_mode", DEFAULT_MODE)
        return cls(mode, load_tokens(root))

    def principal_for(self, token: str | None) -> Principal | None:
        if token is None:
            return None
        owner = self._tokens.get(token)
        return Principal(owner) if owner else None

    def can_read(self, principal: Principal | None, repo: RepoRef) -> bool:
        if self.mode == "open":
            return True
        if repo.visibility == "public":
            return True
        return principal is not None and principal.name == repo.owner

    def can_write(self, principal: Principal | None, repo: RepoRef) -> bool:
        if self.mode == "open":
            return True
        return principal is not None and principal.name == repo.owner
