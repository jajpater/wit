"""DumbRcloneRemote: dezelfde Remote-interface, maar met rclone als transport.

Past op elk rclone-backend (S3, B2, Drive, SFTP, WebDAV, of een lokaal pad). Omdat de
objecten onveranderlijke, content-addressed blobs zijn, is rclone's zwakte (geen in-place
delta, geen rename-detectie) hier irrelevant — er wordt alleen toegevoegd of overgeslagen.

Een dumbe remote heeft geen atomaire ref-CAS: de compare-and-swap is best effort
(lees-dan-schrijf). Veilig voor mirror/backup en single-writer; multi-writer is M6.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from .objects import ObjectStore
from .remote import Remote


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
            store.ingest(kind, oid, tmp)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

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
