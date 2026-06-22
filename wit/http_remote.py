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

Only the stdlib (``urllib``) is used — no new runtime dependency. The bulk
``upload_objects`` / ``download_objects`` paths still fall back to the per-object
loop from ``ObjectTransport``; a batched request is a later optimization (M7).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterable

from .objects import ObjectStore
from .remote import Remote


class HttpRemote(Remote):
    """A repository hosted by a hub, addressed as ``https://host/owner/name``.

    A bearer token (for private repos and pushes) is read from ``$WIT_TOKEN`` and
    sent as ``Authorization: Bearer …``; public-read access needs no token.
    """

    def __init__(self, base_url: str, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token if token is not None else os.environ.get("WIT_TOKEN")

    def _object_url(self, kind: str, oid: str) -> str:
        return f"{self.base_url}/objects/{kind}/{oid}"

    def _request(self, method: str, url: str, data: bytes | None = None):
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/octet-stream")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        return urllib.request.urlopen(req)

    # -- ObjectTransport --------------------------------------------------

    def has(self, kind: str, oid: str) -> bool:
        try:
            with self._request("HEAD", self._object_url(kind, oid)):
                return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
            raise

    def upload(self, store: ObjectStore, kind: str, oid: str) -> None:
        data = store.get(kind, oid)
        with self._request("PUT", self._object_url(kind, oid), data):
            pass

    def download(self, store: ObjectStore, kind: str, oid: str) -> None:
        with self._request("GET", self._object_url(kind, oid)) as resp:
            data = resp.read()
        # store.put re-hashes; a corrupted transfer lands under a different id,
        # so verify the server returned the bytes we asked for.
        stored = store.put(kind, data)
        if stored != oid:
            store._path(kind, stored).unlink(missing_ok=True)
            raise ValueError(
                f"hash mismatch after download of {kind}: "
                f"expected {oid}, got {stored}"
            )

    def list_objects(self, kind: str) -> Iterable[str]:
        with self._request("GET", f"{self.base_url}/objects/{kind}/") as resp:
            text = resp.read().decode("utf-8")
        return [line for line in text.splitlines() if line]

    # -- RefStore ---------------------------------------------------------

    def read_ref(self, ref: str) -> str | None:
        try:
            with self._request("GET", f"{self.base_url}/{ref}") as resp:
                value = resp.read().decode("utf-8").strip()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        return value or None

    def compare_and_swap_ref(
        self, ref: str, expected: str | None, new: str
    ) -> bool:
        body = json.dumps({"expected": expected, "new": new}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/{ref}", data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req) as resp:
            return bool(json.loads(resp.read()).get("ok"))
