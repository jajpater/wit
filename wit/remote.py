"""Remotes: objecttransport en ref-opslag, strikt gescheiden (DOEL.md).

Een remote doet twee fundamenteel verschillende dingen:

* ``ObjectTransport`` — dom, idempotent kopiëren van onveranderlijke objecten op hash;
* ``RefStore`` — atomair lezen en compare-and-swappen van een ref.

Een dumbe remote (`FilesystemRemote`, en straks rclone) kan de ref-CAS alleen *best
effort* (lees-dan-schrijf): veilig voor single-writer/backup, niet voor multi-writer —
daarvoor komt de `wit-server` in M6.
"""

from __future__ import annotations

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

    def compare_and_swap_ref(
        self, ref: str, expected: str | None, new: str
    ) -> bool:
        if self.read_ref(ref) != expected:
            return False
        dest = self.path / ref
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = self.path / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=tmp_dir)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(new + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.rename(tmp, dest)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return True
