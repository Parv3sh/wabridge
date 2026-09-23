"""Structured progress reporting shared by the CLI, the wizard and the GUI engine (`serve`).

Pipeline functions accept an `out` callable for human-readable lines. A `Reporter` is such a
callable that *also* understands progress percentages and structured data, so the same
pipeline code can drive a terminal (TextReporter) or a JSON-lines stream (JsonReporter in
serve.py) without branching.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any, TextIO


class Reporter:
    def __call__(self, text: str = "") -> None:
        self.log(text)

    def log(self, text: str) -> None:              # pragma: no cover - interface
        raise NotImplementedError

    def progress(self, stage: str, pct: float, detail: str | None = None) -> None:
        """0..100 for `stage`. Default: no-op (text reporters override to print)."""

    def data(self, key: str, value: Any) -> None:
        """Structured side-channel (e.g. chat statistics). Default: no-op."""


class TextReporter(Reporter):
    """Prints lines; prints progress every 5 percentage points, like the original wizard."""

    def __init__(self, stream: TextIO | None = None):
        self.stream = stream or sys.stdout
        self._last: dict[str, int] = {}

    def log(self, text: str) -> None:
        print(text, file=self.stream, flush=True)

    def progress(self, stage: str, pct: float, detail: str | None = None) -> None:
        p = int(pct)
        if p != self._last.get(stage) and p % 5 == 0:
            self._last[stage] = p
            self.log(f"  {p}%" + (f"  {detail}" if detail else ""))


def progress_callback(out: Callable[[str], None], stage: str) -> Callable[[float], None]:
    """Adapt any `out` (plain print or Reporter) into a pymobiledevice3-style percent callback."""
    if isinstance(out, Reporter):
        return lambda p: out.progress(stage, float(p))
    last = [-1]

    def cb(p: float) -> None:
        pct = int(p)
        if pct != last[0] and pct % 5 == 0:
            last[0] = pct
            out(f"  {pct}%")

    return cb


def emit_data(out: Callable[[str], None], key: str, value: Any) -> None:
    if isinstance(out, Reporter):
        out.data(key, value)
