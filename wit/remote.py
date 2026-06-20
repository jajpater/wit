"""Remotes: objecttransport en ref-opslag, strikt gescheiden (DOEL.md).

Een remote doet twee fundamenteel verschillende dingen:

* ``ObjectTransport`` — dom, idempotent kopiëren van onveranderlijke objecten op hash;
* ``RefStore`` — atomair lezen en compare-and-swappen van een ref.

Een dumbe remote (`FilesystemRemote`, en straks rclone) kan de ref-CAS alleen *best
effort* (lees-dan-schrijf): veilig voor single-writer/backup, niet voor multi-writer —
daarvoor komt de `wit-server` in M6.
"""

from __future__ import annotations

import fcntl
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

from .objects import ObjectStore

MAIN_REF = "refs/heads/main"


class ObjectTransport(ABC):
    @abstractmethod
    def has(self, kind: str, oid: str) -> bool: ...

    @abstractmethod
    def upload(self, store: ObjectStore, kind: str, oid: str) -> None:
        """Kopieer een lokaal object naar de remote."""

    @abstractmethod
    def download(self, store: ObjectStore, kind: str, oid: str) -> None:
        """Kopieer een remote object naar de lokale store."""


class RefStore(ABC):
    @abstractmethod
    def read_ref(self, ref: str) -> str | None: ...

    @abstractmethod
    def compare_and_swap_ref(
        self, ref: str, expected: str | None, new: str
    ) -> bool:
        """Zet ``ref`` op ``new`` alleen als hij nu op ``expected`` staat."""


class Remote(ObjectTransport, RefStore, ABC):
    """Een remote = objecttransport + ref-opslag."""


class FilesystemRemote(Remote):
    """Een remote die simpelweg een directory op schijf is (eigen objects/ + refs/)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.store = ObjectStore(self.path)

    # -- ObjectTransport (streamende bestandskopie) --
    def has(self, kind: str, oid: str) -> bool:
        return self.store.has(kind, oid)

    def upload(self, store: ObjectStore, kind: str, oid: str) -> None:
        self.store.ingest(kind, oid, store.path_for(kind, oid))

    def download(self, store: ObjectStore, kind: str, oid: str) -> None:
        store.ingest(kind, oid, self.store.path_for(kind, oid))

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
        # Best effort: lees-dan-schrijf zonder lock (zie klassedoc). Veilig voor
        # single-writer; voor multi-writer is er WitServerRemote (M6).
        if self.read_ref(ref) != expected:
            return False
        self._write_ref(ref, new)
        return True


class WitServerRemote(FilesystemRemote):
    """Smart remote: dezelfde opslag, maar een écht atomaire ref-CAS via een lock.

    De compare-and-swap leest-vergelijkt-schrijft onder een ``flock``, zodat
    gelijktijdige pushes serialiseren en er nooit een lost update optreedt — de twee
    heilige taken van de mini-server (DOEL.md), waarvan dit de eerste is. (De tweede,
    veilige GC, is later.) Een netwerkdaemon zou exact deze logica omhullen; hier draait
    de lock op hetzelfde filesystem als de objectopslag.
    """

    def compare_and_swap_ref(
        self, ref: str, expected: str | None, new: str
    ) -> bool:
        locks = self.path / "locks"
        locks.mkdir(parents=True, exist_ok=True)
        lockfile = locks / (ref.replace("/", "_") + ".lock")
        with open(lockfile, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                if self.read_ref(ref) != expected:
                    return False
                self._write_ref(ref, new)
                return True
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def make_remote(spec: str) -> Remote:
    """Bouw een remote uit een spec:

    * ``rclone:<backend>`` -> DumbRcloneRemote (bv. ``rclone:b2:bucket/repo``)
    * ``server:<pad>``     -> WitServerRemote (atomaire ref-CAS)
    * ``fs:<pad>`` of een kaal pad -> FilesystemRemote
    """
    if spec.startswith("rclone:"):
        from .rclone import DumbRcloneRemote

        return DumbRcloneRemote(spec[len("rclone:"):])
    if spec.startswith("server:"):
        return WitServerRemote(Path(spec[len("server:"):]))
    if spec.startswith("fs:"):
        return FilesystemRemote(Path(spec[len("fs:"):]))
    return FilesystemRemote(Path(spec))
