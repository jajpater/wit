"""Remotes: object transport and ref storage, strictly separated (DOEL.md).

A remote does two fundamentally different things:

* ``ObjectTransport`` — dumb, idempotent copying of immutable objects by hash;
* ``RefStore`` — atomic reading and compare-and-swapping of a ref.

A dumb remote (`FilesystemRemote`, and later rclone) can only do the ref-CAS *best
effort* (read-then-write): safe for single-writer/backup, not for multi-writer —
for that we will have the `wit-server` in M6.
"""

from __future__ import annotations

import fcntl
import os
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Iterable
from pathlib import Path

from .objects import ObjectStore

MAIN_REF = "refs/heads/main"

# The small, metadata object types that are fetched wholesale during fetch (DOEL.md).
META_KINDS = ("commits", "trees")


class ObjectTransport(ABC):
    @abstractmethod
    def has(self, kind: str, oid: str) -> bool: ...

    @abstractmethod
    def upload(self, store: ObjectStore, kind: str, oid: str) -> None:
        """Copy a local object to the remote."""

    @abstractmethod
    def download(self, store: ObjectStore, kind: str, oid: str) -> None:
        """Copy a remote object to the local store."""

    @abstractmethod
    def list_objects(self, kind: str) -> Iterable[str]:
        """All object IDs of a given kind on the remote."""

    # -- Bulk-transport (M7). Default: per-object loops (fine for a local
    # filesystem). rclone overrides this with a single call for everything, so the
    # per-object latency of cloud backends doesn't become the bottleneck. --
    def upload_objects(
        self, store: ObjectStore, items: Iterable[tuple[str, str]]
    ) -> None:
        for kind, oid in items:
            if not self.has(kind, oid):
                self.upload(store, kind, oid)

    def download_objects(
        self, store: ObjectStore, items: Iterable[tuple[str, str]]
    ) -> None:
        for kind, oid in items:
            if not store.has(kind, oid):
                self.download(store, kind, oid)

    def fetch_metadata(self, store: ObjectStore) -> None:
        """Fetch all commit and tree objects (small; wholesale)."""
        for kind in META_KINDS:
            for oid in self.list_objects(kind):
                if not store.has(kind, oid):
                    self.download(store, kind, oid)


class RefStore(ABC):
    @abstractmethod
    def read_ref(self, ref: str) -> str | None: ...

    @abstractmethod
    def compare_and_swap_ref(
        self, ref: str, expected: str | None, new: str
    ) -> bool:
        """Set ``ref`` to ``new`` only if it is currently at ``expected``."""


class Remote(ObjectTransport, RefStore, ABC):
    """A remote = object transport + ref storage."""


class FilesystemRemote(Remote):
    """A remote that is simply a directory on disk (its own objects/ + refs/)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.store = ObjectStore(self.path)

    # -- ObjectTransport (streaming file copy) --
    def has(self, kind: str, oid: str) -> bool:
        return self.store.has(kind, oid)

    def upload(self, store: ObjectStore, kind: str, oid: str) -> None:
        self.store.ingest(kind, oid, store.path_for(kind, oid))

    def download(self, store: ObjectStore, kind: str, oid: str) -> None:
        store.ingest(kind, oid, self.store.path_for(kind, oid))

    def list_objects(self, kind: str) -> Iterable[str]:
        return self.store.iter_objects(kind)

    # -- RefStore (best-effort CAS) --
    def read_ref(self, ref: str) -> str | None:
        path = self.path / ref
        return path.read_text().strip() if path.exists() else None

    def _write_ref(self, ref: str, value: str) -> None:
        dest = self.path / ref
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = self.path / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=tmp_dir)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(value + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.rename(tmp, dest)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def compare_and_swap_ref(
        self, ref: str, expected: str | None, new: str
    ) -> bool:
        # Best effort: read-then-write without a lock (see class doc). Safe for
        # single-writer; for multi-writer there is WitServerRemote (M6).
        if self.read_ref(ref) != expected:
            return False
        self._write_ref(ref, new)
        return True


class WitServerRemote(FilesystemRemote):
    """Smart remote: same storage, but a truly atomic ref-CAS via a lock.

    The compare-and-swap reads-compares-writes under an ``flock``, so that
    concurrent pushes are serialized and a lost update never occurs — the two
    sacred tasks of the mini-server (DOEL.md), of which this is the first. (The second,
    safe GC, is later.) A network daemon would wrap exactly this logic; here
    the lock runs on the same filesystem as the object storage.
    """

    def _ref_lock(self, ref: str):
        locks = self.path / "locks"
        locks.mkdir(parents=True, exist_ok=True)
        return open(locks / (ref.replace("/", "_") + ".lock"), "w")

    def compare_and_swap_ref(
        self, ref: str, expected: str | None, new: str
    ) -> bool:
        with self._ref_lock(ref) as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                if self.read_ref(ref) != expected:
                    return False
                self._write_ref(ref, new)
                return True
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def gc(self, grace_seconds: float | None = None):
        """The second sacred server task: safe GC on the remote itself.

        Mark from the remote refs and sweep the remote objects, all under the same
        ``flock`` as the ref-CAS on ``main``. This way no push can move the ref
        during the GC; objects from a not-yet-completed push are young and fall within the
        grace window, so they aren't swept (no GC<->push race). Dumb remotes
        intentionally do not offer this (DOEL.md)."""
        from .gc import DEFAULT_GRACE_SECONDS, mark_reachable, refs_in, sweep

        grace = DEFAULT_GRACE_SECONDS if grace_seconds is None else grace_seconds
        with self._ref_lock(MAIN_REF) as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                roots = refs_in(self.path / "refs")
                reachable = mark_reachable(self.store, roots)
                return sweep(self.store, reachable, grace)
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def make_remote(spec: str) -> Remote:
    """Build a remote from a spec:

    * ``rclone:<backend>`` -> DumbRcloneRemote (e.g. ``rclone:b2:bucket/repo``)
    * ``server:<path>``    -> WitServerRemote (atomic ref-CAS)
    * ``fs:<path>`` or bare path -> FilesystemRemote
    """
    if spec.startswith("rclone:"):
        from .rclone import DumbRcloneRemote

        return DumbRcloneRemote(spec[len("rclone:"):])
    if spec.startswith("server:"):
        return WitServerRemote(Path(spec[len("server:"):]))
    if spec.startswith("fs:"):
        return FilesystemRemote(Path(spec[len("fs:"):]))
    return FilesystemRemote(Path(spec))
