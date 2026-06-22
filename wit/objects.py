"""Content-addressed object store.

Objects are addressed by their BLAKE3-hash and stored as immutable,
hash-named files under ``objects/<kind>/<ab>/<rest>``. Blobs are raw bytes
(``id == b3sum`` of the loose file -> externally verifiable); trees and commits are
canonical JSON. Object IDs are self-describing: ``b3:<hex>``.

Writing always goes through ``tmp/`` + atomic rename, so an aborted write
never leaves a partial object in its final location.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import BinaryIO

from .i18n import _

from blake3 import blake3

#: The object types, each in its own subdir of ``objects/`` (separated
#: dirs so metadata can be transported wholesale and blobs selectively).
KINDS = ("blobs", "trees", "commits")

_ALGO = "b3"
_CHUNK = 1024 * 1024


def hash_bytes(data: bytes) -> str:
    """The self-describing object-id of ``data``."""
    return f"{_ALGO}:{blake3(data).hexdigest()}"


def hash_file(path: Path) -> str:
    """Streaming hash of a file (memory-efficient for large documents)."""
    hasher = blake3()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            hasher.update(chunk)
    return f"{_ALGO}:{hasher.hexdigest()}"


def _hex(oid: str) -> str:
    algo, sep, hexpart = oid.partition(":")
    if not sep or algo != _ALGO or not hexpart:
        raise ValueError(_("invalid object-id: {oid!r}").format(oid=oid))
    return hexpart


class ObjectStore:
    """The truth: ``objects/`` + ``refs/``. Everything below this is rebuildable cache."""

    def __init__(self, wit_dir: Path) -> None:
        self.wit_dir = Path(wit_dir)
        self.objects_dir = self.wit_dir / "objects"
        self.tmp_dir = self.wit_dir / "tmp"

    def _path(self, kind: str, oid: str) -> Path:
        if kind not in KINDS:
            raise ValueError(_("unknown object kind: {kind!r}").format(kind=kind))
        h = _hex(oid)
        return self.objects_dir / kind / h[:2] / h[2:]

    def has(self, kind: str, oid: str) -> bool:
        return self._path(kind, oid).exists()

    def get(self, kind: str, oid: str) -> bytes:
        path = self._path(kind, oid)
        if not path.exists():
            raise KeyError(oid)
        return path.read_bytes()

    def copy_to(self, kind: str, oid: str, dest: Path) -> None:
        """Materialize an object as a real file at ``dest`` (streaming, no symlink)."""
        src = self._path(kind, oid)
        if not src.exists():
            raise KeyError(oid)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)

    def recompute_id(self, kind: str, oid: str) -> str:
        """Recompute the ID of a stored object by streaming it (for fsck)."""
        path = self._path(kind, oid)
        if not path.exists():
            raise KeyError(oid)
        return hash_file(path)

    def verify_object(self, kind: str, oid: str) -> None:
        """Check a stored object; delete and raise on corruption.

        For objects that ended up in the store outside of ``ingest`` (bulk download via
        rclone): recompute the ID and compare; on mismatch, the corrupt file is
        deleted so the store remains consistent."""
        actual = self.recompute_id(kind, oid)
        if actual != oid:
            self._path(kind, oid).unlink(missing_ok=True)
            raise ValueError(
                _("hash mismatch after download of {kind}: expected {oid}, got {actual}").format(kind=kind, oid=oid, actual=actual)
            )

    def path_for(self, kind: str, oid: str) -> Path:
        """The path of a stored object (for transport between stores)."""
        return self._path(kind, oid)

    def ingest(self, kind: str, oid: str, src: Path, *, verify: bool = True) -> None:
        """Atomically place an existing object file (streaming copy) under its ID.

        By default, the content is re-hashed after copying and before the rename
        and compared against ``oid`` (defense against corruption-in-transit): on mismatch,
        the tmp file is deleted and a ``ValueError`` is raised, so a corrupt
        object never appears under its claimed ID. ``verify=False`` skips
        the pass (e.g., for a trusted local copy)."""
        dest = self._path(kind, oid)
        if dest.exists():
            return
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.tmp_dir)
        os.close(fd)
        try:
            shutil.copyfile(src, tmp)
            if verify:
                actual = hash_file(Path(tmp))
                if actual != oid:
                    raise ValueError(
                        _("hash mismatch during ingest of {kind}: expected {oid}, got {actual}").format(kind=kind, oid=oid, actual=actual)
                    )
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.rename(tmp, dest)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def put(self, kind: str, data: bytes) -> str:
        """Save bytes; returns the object ID. Idempotent (dedup on hash)."""
        oid = hash_bytes(data)
        dest = self._path(kind, oid)
        if dest.exists():
            return oid
        self._atomic_write(dest, lambda out: out.write(data))
        return oid

    def put_file(self, src: Path, kind: str = "blobs") -> str:
        """Stream a file into the store: hash and copy in a single pass."""
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        hasher = blake3()
        fd, tmp = tempfile.mkstemp(dir=self.tmp_dir)
        try:
            with os.fdopen(fd, "wb") as out, open(src, "rb") as f:
                while chunk := f.read(_CHUNK):
                    hasher.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            oid = f"{_ALGO}:{hasher.hexdigest()}"
            dest = self._path(kind, oid)
            if dest.exists():
                os.unlink(tmp)
                return oid
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.rename(tmp, dest)
            return oid
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def iter_objects(self, kind: str) -> Iterator[str]:
        """All object IDs of a given kind (for fsck and GC-reachability)."""
        base = self.objects_dir / kind
        if not base.exists():
            return
        for prefix in sorted(base.iterdir()):
            if not prefix.is_dir():
                continue
            for obj in sorted(prefix.iterdir()):
                yield f"{_ALGO}:{prefix.name}{obj.name}"

    def _atomic_write(self, dest: Path, write: Callable[[BinaryIO], object]) -> None:
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.tmp_dir)
        try:
            with os.fdopen(fd, "wb") as out:
                write(out)
                out.flush()
                os.fsync(out.fileno())
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.rename(tmp, dest)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
