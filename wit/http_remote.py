"""Client-side remote over HTTP, talking to a hub.

This is the network counterpart of ``FilesystemRemote``: it implements the same
``ObjectTransport`` + ``RefStore`` ABCs, so ``sync.py`` (push/pull/clone) works
against a hub without changes. The hub exposes, per repo, exactly these two
abstractions (see ARCHITECTURE-hub.md):

    ObjectTransport                       RefStore
      HEAD  …/objects/<kind>/<oid>          GET  …/refs/<branch>
      GET   …/objects/<kind>/<oid>          POST …/refs/<branch>  {expected, new}
      PUT   …/objects/<kind>/<oid>
      GET   …/objects/<kind>/

Skeleton: the wire format is fixed by the design, but the methods are stubs. The
``upload_objects`` / ``download_objects`` bulk paths from ``ObjectTransport`` should
be overridden with a batched request before this is used at scale.
"""

from __future__ import annotations

from collections.abc import Iterable

from .objects import ObjectStore
from .remote import Remote


class HttpRemote(Remote):
    """A repository hosted by a hub, addressed as ``https://host/owner/name``."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def _object_url(self, kind: str, oid: str) -> str:
        return f"{self.base_url}/objects/{kind}/{oid}"

    def _ref_url(self, ref: str) -> str:
        return f"{self.base_url}/{ref}"

    # -- ObjectTransport --------------------------------------------------

    def has(self, kind: str, oid: str) -> bool:
        raise NotImplementedError("HttpRemote.has — see ARCHITECTURE-hub.md")

    def upload(self, store: ObjectStore, kind: str, oid: str) -> None:
        raise NotImplementedError("HttpRemote.upload")

    def download(self, store: ObjectStore, kind: str, oid: str) -> None:
        raise NotImplementedError("HttpRemote.download")

    def list_objects(self, kind: str) -> Iterable[str]:
        raise NotImplementedError("HttpRemote.list_objects")

    # -- RefStore ---------------------------------------------------------

    def read_ref(self, ref: str) -> str | None:
        raise NotImplementedError("HttpRemote.read_ref")

    def compare_and_swap_ref(
        self, ref: str, expected: str | None, new: str
    ) -> bool:
        raise NotImplementedError("HttpRemote.compare_and_swap_ref")
