"""Local SciNCL embeddings via sentence-transformers (MIT, citation-aware, 768-dim).

Fallback / future-default backend. Imports torch + sentence-transformers lazily so the
base environment (without the ``local-embed`` extra) still imports the pipeline. Uses
Apple MPS when available, else CPU. Covers every row (no external-id dependency).

Install with: ``uv pip install "sentence-transformers>=3.0" "torch>=2.2"``
"""

from __future__ import annotations

import numpy as np
import polars as pl

from pipeline.common import log
from pipeline.embedding.base import EmbeddingResult


class ScinclLocalBackend:
    name = "scincl_local"

    def __init__(self, model: str = "malteos/scincl", dim: int = 768,
                 batch_size: int = 64):
        self.model = model
        self.dim = dim
        self.batch_size = batch_size

    def _device(self) -> str:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"

    def embed(self, corpus: pl.DataFrame) -> EmbeddingResult:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "scincl_local backend needs sentence-transformers + torch. "
                "Install with: uv pip install 'sentence-transformers>=3.0' 'torch>=2.2'"
            ) from e

        device = self._device()
        log.info(f"SciNCL: loading {self.model} on {device}")
        st = SentenceTransformer(self.model, device=device)

        texts = corpus["text"].to_list()
        vecs = st.encode(
            texts,
            batch_size=self.batch_size,
            show_progress_bar=True,
            convert_to_numpy=True,
            normalize_embeddings=False,  # s03 normalizes uniformly across backends
        ).astype(np.float32)

        covered = np.ones(corpus.height, dtype=bool)
        return EmbeddingResult(vecs, covered, self.name, self.model)
