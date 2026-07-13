"""Shared IO helpers: Arrow IPC, JSON, .npy, JSONL, and checksums.

Arrow files are written in IPC *file* format (a.k.a. Feather v2) so the browser can
``tableFromIPC`` them zero-copy. JSON is written pydantic-aware.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np
import pyarrow as pa
import pyarrow.feather as feather


def write_arrow(table: pa.Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # IMPORTANT: no compression. apache-arrow (JS) cannot decode compressed record
    # batches ("Record batch compression not implemented"), so we write uncompressed
    # Arrow IPC. Files stay small enough at MVP scale; gzip/brotli at the HTTP layer
    # (CDN) recovers most of the size benefit for the wire.
    feather.write_feather(table, path, compression="uncompressed")


def read_arrow(path: Path) -> pa.Table:
    return feather.read_table(path)


def write_json(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # pydantic models expose model_dump(); fall back to default JSON for plain objects.
    data = obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def write_npy(arr: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, arr)


def read_npy(path: Path) -> np.ndarray:
    return np.load(path)


def write_jsonl(rows: Iterable[dict], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")
            n += 1
    return n


def read_jsonl(path: Path) -> Iterator[dict]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
