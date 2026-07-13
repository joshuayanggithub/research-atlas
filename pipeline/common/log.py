"""Tiny consistent stage logger (no external dep)."""

from __future__ import annotations

import sys
import time


def stage(name: str) -> None:
    print(f"\n=== [{name}] ===", flush=True)


def info(msg: str) -> None:
    print(f"  {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"  ! {msg}", file=sys.stderr, flush=True)


class timer:
    """Context manager that prints elapsed wall time for a labeled step."""

    def __init__(self, label: str):
        self.label = label

    def __enter__(self):
        self.t0 = time.monotonic()
        return self

    def __exit__(self, *exc):
        dt = time.monotonic() - self.t0
        print(f"  ({self.label}: {dt:.1f}s)", flush=True)
        return False
