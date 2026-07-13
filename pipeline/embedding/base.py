"""Embedding backend protocol.

A backend turns a corpus (papers with text + external ids) into a dense float32 matrix
[N, dim], row-aligned to the corpus ``node_id``. Backends may fail to cover every paper;
they return a boolean ``covered`` mask so the dispatcher (s03) can decide whether to
accept the result or fall back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
import polars as pl


@dataclass
class EmbeddingResult:
    vectors: np.ndarray      # [N, dim] float32, row-aligned to corpus order
    covered: np.ndarray      # [N] bool — True where a real embedding was produced
    backend: str
    model: str

    @property
    def coverage(self) -> float:
        return float(self.covered.mean()) if len(self.covered) else 0.0


class EmbeddingBackend(Protocol):
    name: str
    model: str
    dim: int

    def embed(self, corpus: pl.DataFrame) -> EmbeddingResult:
        """Embed a corpus DataFrame (must have columns: node_id, text, doi, arxiv_id, paper_id).

        Returns vectors row-aligned to ``corpus`` order (which is node_id order).
        """
        ...
