"""Content-addressed object store.

Objecten worden geadresseerd op hun BLAKE3-hash en opgeslagen als onveranderlijke,
hash-genaamde bestanden onder ``objects/<kind>/<ab>/<rest>``. Blobs zijn ruwe bytes
(``id == b3sum`` van het losse bestand → extern verifieerbaar); trees en commits zijn
canonieke JSON. Object-id's zijn zelf-beschrijvend: ``b3:<hex>``.

Schrijven gaat altijd via ``tmp/`` + atomic rename, zodat een afgebroken schrijfactie
nooit een half object op zijn definitieve plek achterlaat (zie M0-criterium in DOEL.md).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import BinaryIO

from blake3 import blake3

#: De objecttypes, elk in een eigen subdir van ``objects/`` (zie DOEL.md: gescheiden
#: dirs zodat metadata wholesale en blobs selectief getransporteerd kunnen worden).
KINDS = ("blobs", "trees", "commits")

_ALGO = "b3"
_CHUNK = 1024 * 1024


def hash_bytes(data: bytes) -> str:
    """De zelf-beschrijvende object-id van ``data``."""
    return f"{_ALGO}:{blake3(data).hexdigest()}"


def hash_file(path: Path) -> str:
    """Streaming-hash van een bestand (geheugen-zuinig voor grote documenten)."""
    hasher = blake3()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            hasher.update(chunk)
    return f"{_ALGO}:{hasher.hexdigest()}"


def _hex(oid: str) -> str:
    algo, sep, hexpart = oid.partition(":")
    if not sep or algo != _ALGO or not hexpart:
        raise ValueError(f"ongeldige object-id: {oid!r}")
    return hexpart


class ObjectStore:
    """De waarheid: ``objects/`` + ``refs/``. Alles hieronder is herbouwbaar cache."""

    def __init__(self, wit_dir: Path) -> None:
        self.wit_dir = Path(wit_dir)
        self.objects_dir = self.wit_dir / "objects"
        self.tmp_dir = self.wit_dir / "tmp"

    def _path(self, kind: str, oid: str) -> Path:
        if kind not in KINDS:
            raise ValueError(f"onbekend objecttype: {kind!r}")
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
        """Materialiseer een object als echt bestand op ``dest`` (streamend, geen symlink)."""
        src = self._path(kind, oid)
        if not src.exists():
            raise KeyError(oid)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)

    def recompute_id(self, kind: str, oid: str) -> str:
        """Herbereken de id van een opgeslagen object door het te streamen (voor fsck)."""
        path = self._path(kind, oid)
        if not path.exists():
            raise KeyError(oid)
        return hash_file(path)

    def put(self, kind: str, data: bytes) -> str:
        """Bewaar bytes; geeft de object-id terug. Idempotent (dedup op hash)."""
        oid = hash_bytes(data)
        dest = self._path(kind, oid)
        if dest.exists():
            return oid
        self._atomic_write(dest, lambda out: out.write(data))
        return oid

    def put_file(self, src: Path, kind: str = "blobs") -> str:
        """Stream een bestand de store in: hash en kopieer in één pass."""
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
        """Alle object-id's van een type (voor fsck en GC-reachability)."""
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
