"""Canonical serialization for tree and commit objects.

Content-addressing requires a byte-stable representation: sorted keys, no
redundant whitespace, UTF-8. This way, the same content yields the same hash on every machine.
"""

from __future__ import annotations

import json
from typing import Any


def dumps(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def loads(data: bytes) -> Any:
    return json.loads(data.decode("utf-8"))
