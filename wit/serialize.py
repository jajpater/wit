"""Canonieke serialisatie voor tree- en commit-objecten.

Content-addressing vereist een byte-stabiele representatie: gesorteerde keys, geen
overbodige witruimte, UTF-8. Zo geeft dezelfde inhoud op elke machine dezelfde hash.
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
