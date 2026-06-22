"""DumbRcloneRemote: same Remote interface, but with rclone as transport.

Fits any rclone backend (S3, B2, Drive, SFTP, WebDAV, or a local path). Because the
objects are immutable, content-addressed blobs, rclone's weakness (no in-place
delta, no rename detection) is irrelevant here — it only adds or skips.

A dumb remote has no atomic ref-CAS: the compare-and-swap is best effort
(read-then-write). Safe for mirror/backup and single-writer; multi-writer is M6.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from .objects import ObjectStore
from .remote import META_KINDS, Remote


class RcloneError(Exception):
    pass


def have_rclone() -> bool:
    return shutil.which("rclone") is not None


class DumbRcloneRemote(Remote):
    def __init__(self, base: str, rclone: str = "rclone") -> None:
        self.base = base.rstrip("/")
        self.rclone = rclone

    def _path(self, *parts: str) -> str:
        return self.base + "/" + "/".join(parts)

    def _obj(self, kind: str, oid: str) -> tuple[str, str]:
        h = oid.split(":", 1)[1]
        return self._path("objects", kind, h[:2], h[2:]), h

    def _run(self, args: list[str], **kw) -> subprocess.CompletedProcess:
        return subprocess.run([self.rclone, *args], capture_output=True, **kw)

    # -- ObjectTransport --
    def has(self, kind: str, oid: str) -> bool:
        h = oid.split(":", 1)[1]
        listing = self._run(["lsf", self._path("objects", kind, h[:2]) + "/"])
        if listing.returncode != 0:
            return False
        return h[2:] in listing.stdout.decode().split()

    def upload(self, store: ObjectStore, kind: str, oid: str) -> None:
        remote_obj, _ = self._obj(kind, oid)
        result = self._run(["copyto", str(store.path_for(kind, oid)), remote_obj])
        if result.returncode != 0:
            raise RcloneError(result.stderr.decode())

    def download(self, store: ObjectStore, kind: str, oid: str) -> None:
        remote_obj, _ = self._obj(kind, oid)
        store.tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=store.tmp_dir)
        os.close(fd)
        try:
            result = self._run(["copyto", remote_obj, tmp])
            if result.returncode != 0:
                raise RcloneError(result.stderr.decode())
            store.ingest(kind, oid, Path(tmp))
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # -- RefStore (best-effort CAS) --
    def list_objects(self, kind: str) -> Iterable[str]:
        result = self._run(
            ["lsf", "-R", "--files-only", self._path("objects", kind) + "/"]
        )
        if result.returncode != 0:
            return []
        out = []
        for line in result.stdout.decode().splitlines():
            line = line.strip()
            if not line:
                continue
            ab, _, rest = line.partition("/")
            out.append(f"b3:{ab}{rest}")
        return out

    # -- Bulk-transport (M7): one rclone call per object kind instead of per object --
    def _bulk_copy(self, src: str, dst: str, rels: list[str]) -> None:
        if not rels:
            return
        fd, listfile = tempfile.mkstemp()
        try:
            with os.fdopen(fd, "w") as f:
                f.write("\n".join(rels) + "\n")
            # rclone copy is idempotent: existing objects are skipped,
            # so no per-object has() round-trips are needed.
            result = self._run(["copy", "--files-from", listfile, src, dst])
            if result.returncode != 0:
                raise RcloneError(result.stderr.decode())
        finally:
            os.unlink(listfile)

    def _group(self, items: Iterable[tuple[str, str]]) -> dict[str, list[str]]:
        by_kind: dict[str, list[str]] = defaultdict(list)
        for kind, oid in items:
            h = oid.split(":", 1)[1]
            by_kind[kind].append(f"{h[:2]}/{h[2:]}")
        return by_kind

    def upload_objects(
        self, store: ObjectStore, items: Iterable[tuple[str, str]]
    ) -> None:
        for kind, rels in self._group(items).items():
            self._bulk_copy(
                str(store.objects_dir / kind), self._path("objects", kind), rels
            )

    def download_objects(
        self, store: ObjectStore, items: Iterable[tuple[str, str]]
    ) -> None:
        items = list(items)
        for kind, rels in self._group(items).items():
            (store.objects_dir / kind).mkdir(parents=True, exist_ok=True)
            self._bulk_copy(
                self._path("objects", kind), str(store.objects_dir / kind), rels
            )
        # Bulk-copy bypasses ingest: verify the downloaded objects anyway.
        for kind, oid in items:
            store.verify_object(kind, oid)

    def fetch_metadata(self, store: ObjectStore) -> None:
        for kind in META_KINDS:
            dest = store.objects_dir / kind
            dest.mkdir(parents=True, exist_ok=True)
            before = set(store.iter_objects(kind))
            result = self._run(["copy", self._path("objects", kind), str(dest)])
            if result.returncode != 0:
                raise RcloneError(result.stderr.decode())
            for oid in set(store.iter_objects(kind)) - before:
                store.verify_object(kind, oid)

    # -- RefStore (best-effort CAS) --
    def read_ref(self, ref: str) -> str | None:
        result = self._run(["cat", self._path(ref)])
        if result.returncode != 0:
            return None
        return result.stdout.decode().strip() or None

    def compare_and_swap_ref(
        self, ref: str, expected: str | None, new: str
    ) -> bool:
        if self.read_ref(ref) != expected:
            return False
        result = self._run(["rcat", self._path(ref)], input=(new + "\n").encode())
        if result.returncode != 0:
            raise RcloneError(result.stderr.decode())
        return True
