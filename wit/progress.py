"""Small, throttled transfer display on stderr, without extra dependencies."""

from __future__ import annotations

import sys
import time

from .i18n import _


def _size(value: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if value < 1024 or unit == "PiB":
            return f"{value:.1f} {unit}"
        value /= 1024


class TransferProgress:
    def __init__(
        self, total: int, *, enabled: bool, byte_count: bool = True
    ) -> None:
        self.total = total
        self.enabled = enabled and total > 0
        self.byte_count = byte_count
        self.done = 0

    def __enter__(self) -> TransferProgress:
        if self.enabled:
            self.started = time.monotonic()
            self._render(self.started)
        return self

    def advance(self, count: int) -> None:
        if not self.enabled:
            return
        self.done += count
        now = time.monotonic()
        if self.done == self.total or now - self.last_render >= 0.1:
            self._render(now)

    def _render(self, now: float) -> None:
        percent = 100 * self.done // self.total
        if self.byte_count:
            speed = self.done / max(now - self.started, 0.001)
            detail = f"{_size(self.done)} / {_size(self.total)}  {_size(speed)}/s"
        else:
            detail = _("{done}/{total} objects").format(
                done=self.done, total=self.total)
        print(f"\r\033[K  push: {percent:3d}%  {detail}",
              end="", file=sys.stderr, flush=True)
        self.last_render = now

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self.enabled:
            # End the line even on interruption or a failed transfer, so errors
            # and the final CLI result start on their own line.
            print(file=sys.stderr, flush=True)
