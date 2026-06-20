"""``wit fsck`` — verifieer de integriteit van de object store.

Voor elk object: lees het terug, herbereken de BLAKE3-hash en vergelijk met de id
waaronder het is opgeslagen. Een mismatch betekent corruptie. Verweesde ``tmp/``-
bestanden zijn afgebroken schrijfacties en worden (standaard) opgeruimd.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .objects import KINDS, ObjectStore


@dataclass
class FsckReport:
    checked: int = 0
    corrupt: list[str] = field(default_factory=list)
    stray_tmp: int = 0

    @property
    def ok(self) -> bool:
        return not self.corrupt


def fsck(store: ObjectStore, clean_tmp: bool = True) -> FsckReport:
    report = FsckReport()
    for kind in KINDS:
        for oid in store.iter_objects(kind):
            report.checked += 1
            if store.recompute_id(kind, oid) != oid:  # streamend, geen geheugenpiek
                report.corrupt.append(oid)
    if store.tmp_dir.exists():
        for stray in store.tmp_dir.iterdir():
            report.stray_tmp += 1
            if clean_tmp:
                stray.unlink()
    return report
