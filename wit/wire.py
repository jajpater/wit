"""Framing for batched object transport over HTTP.

A batch is a stream of records, each a header line followed by raw bytes:

    ``<kind> <oid> <length>\\n`` then exactly ``<length>`` bytes

Concatenated, with no envelope. Both sides stream one object at a time, so memory
stays bounded regardless of the batch size — the point of batching is to remove the
per-object round-trip, not to buffer everything. See ARCHITECTURE-hub.md (M7).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import BinaryIO

_CHUNK = 1024 * 1024


def frame_header(kind: str, oid: str, length: int) -> bytes:
    return f"{kind} {oid} {length}\n".encode("ascii")


def frame_size(kind: str, oid: str, length: int) -> int:
    """Total wire size of one record (header + payload), for Content-Length."""
    return len(frame_header(kind, oid, length)) + length


def read_frames(
    fp: BinaryIO, limit: int | None = None
) -> Iterator[tuple[str, str, bytes]]:
    """Yield ``(kind, oid, data)`` records until EOF or ``limit`` bytes consumed.

    ``limit`` (a Content-Length) is used on the server so reading stops exactly at
    the request boundary; the client passes ``None`` and reads to EOF.
    """
    consumed = 0
    while limit is None or consumed < limit:
        line = fp.readline()
        if not line:
            return
        consumed += len(line)
        kind, oid, length = line.split()
        n = int(length)
        data = fp.read(n)
        consumed += len(data)
        yield kind.decode("ascii"), oid.decode("ascii"), data


def stream_object(fp_out: BinaryIO, src_path, kind: str, oid: str, size: int) -> None:
    """Write one record (header + streamed file contents) to ``fp_out``."""
    fp_out.write(frame_header(kind, oid, size))
    with open(src_path, "rb") as f:
        while chunk := f.read(_CHUNK):
            fp_out.write(chunk)
