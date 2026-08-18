"""Local SPECTER2 proximity-adapter inference with durable row checkpoints.

This is the same public model family used for Semantic Scholar's ``specter_v2`` vectors,
but it covers papers immediately without waiting for external indexing. The checkpoint is
row-aligned to the stable corpus order, so an interrupted multi-hour GPU run resumes at
the next batch.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import polars as pl
from tqdm import tqdm

from pipeline.common import log
from pipeline.common.io import read_json, write_json
from pipeline.config import INTERIM_DIR
from pipeline.embedding.base import EmbeddingResult

PARTIAL = INTERIM_DIR / "embeddings.specter2.partial.npy"
CHECKPOINT = INTERIM_DIR / "embeddings.specter2.checkpoint.json"


class Specter2LocalBackend:
    name = "specter2_local"

    def __init__(
        self,
        model: str = "allenai/specter2_base",
        adapter: str = "allenai/specter2",
        dim: int = 768,
        batch_size: int = 64,
        device: str = "auto",
        precision: str = "auto",
        checkpoint_every: int = 2048,
        partial_path: Path = PARTIAL,
        checkpoint_path: Path = CHECKPOINT,
    ):
        self.model = model
        self.adapter = adapter
        self.dim = dim
        self.batch_size = batch_size
        self.device = device
        self.precision = precision
        self.checkpoint_every = max(batch_size, checkpoint_every)
        self.partial_path = partial_path
        self.checkpoint_path = checkpoint_path

    def _device(self, torch) -> str:
        if self.device != "auto":
            return self.device
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def _dtype(self, torch, device: str):
        precision = self.precision
        if precision == "auto":
            precision = "fp16" if device == "cuda" else "fp32"
        return {"fp16": torch.float16, "bf16": torch.bfloat16,
                "fp32": torch.float32}[precision]

    def _fingerprint(self, corpus: pl.DataFrame) -> str:
        h = hashlib.sha256()
        h.update(f"{self.model}\0{self.adapter}\0{self.dim}\0{corpus.height}\0".encode())
        for paper_id in corpus["paper_id"]:
            h.update(str(paper_id).encode())
            h.update(b"\0")
        return h.hexdigest()

    def _open_checkpoint(self, corpus: pl.DataFrame) -> tuple[np.memmap, int, str]:
        fingerprint = self._fingerprint(corpus)
        start = 0
        valid = False
        if self.checkpoint_path.exists() and self.partial_path.exists():
            try:
                state = read_json(self.checkpoint_path)
                valid = (state.get("fingerprint") == fingerprint and
                         state.get("shape") == [corpus.height, self.dim])
                if valid:
                    start = min(int(state.get("completed_rows", 0)), corpus.height)
            except (OSError, ValueError, KeyError):
                valid = False
        if valid:
            matrix = np.lib.format.open_memmap(self.partial_path, mode="r+")
            log.info(f"SPECTER2 checkpoint: resuming at row {start:,}/{corpus.height:,}")
        else:
            self.partial_path.parent.mkdir(parents=True, exist_ok=True)
            matrix = np.lib.format.open_memmap(
                self.partial_path, mode="w+", dtype=np.float32,
                shape=(corpus.height, self.dim),
            )
            self._save_checkpoint(fingerprint, corpus.height, 0)
        return matrix, start, fingerprint

    def _save_checkpoint(self, fingerprint: str, n: int, completed: int) -> None:
        write_json({
            "fingerprint": fingerprint,
            "shape": [n, self.dim],
            "completed_rows": completed,
            "model": self.model,
            "adapter": self.adapter,
        }, self.checkpoint_path)

    def embed(self, corpus: pl.DataFrame) -> EmbeddingResult:
        try:
            import torch
            from adapters import AutoAdapterModel
            from transformers import AutoTokenizer
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "specter2_local needs adapters + transformers + torch. "
                "Install with: uv sync --extra local-embed"
            ) from exc

        matrix, start, fingerprint = self._open_checkpoint(corpus)
        if start == corpus.height:
            covered = np.ones(corpus.height, dtype=bool)
            return EmbeddingResult(matrix, covered, self.name,
                                   f"{self.model}+{self.adapter}:proximity")

        device = self._device(torch)
        dtype = self._dtype(torch, device)
        log.info(f"SPECTER2: loading {self.model} + proximity adapter {self.adapter} "
                 f"on {device} ({str(dtype).removeprefix('torch.')})")
        tokenizer = AutoTokenizer.from_pretrained(self.model)
        model = AutoAdapterModel.from_pretrained(self.model)
        loaded_as = model.load_adapter(
            self.adapter, source="hf", load_as="proximity", set_active=True,
        )
        # adapters 1.x may emit a warning while constructing the base model before the
        # adapter is loaded. Verify post-load state so that warning can never mask an
        # accidental base-model-only embedding run.
        if str(loaded_as) != "proximity" or "proximity" not in str(model.active_adapters):
            model.set_active_adapters("proximity")
        if "proximity" not in str(model.active_adapters):
            raise RuntimeError("SPECTER2 proximity adapter loaded but is not active")
        model.eval()
        model.to(device)
        if device != "cpu" and dtype != torch.float32:
            model.to(dtype=dtype)

        titles = corpus["title"].to_list()
        abstracts = corpus["abstract"].fill_null("").to_list()
        sep = tokenizer.sep_token or "[SEP]"
        last_checkpoint = start
        progress = tqdm(total=corpus.height, initial=start, desc="  SPECTER2", unit="paper")
        with torch.inference_mode():
            for begin in range(start, corpus.height, self.batch_size):
                end = min(begin + self.batch_size, corpus.height)
                texts = [f"{titles[i]}{sep}{abstracts[i]}" for i in range(begin, end)]
                inputs = tokenizer(
                    texts,
                    padding=True,
                    truncation=True,
                    return_tensors="pt",
                    return_token_type_ids=False,
                    max_length=512,
                )
                inputs = {key: value.to(device) for key, value in inputs.items()}
                output = model(**inputs)
                vecs = output.last_hidden_state[:, 0, :].float().cpu().numpy()
                if vecs.shape[1] != self.dim:
                    raise ValueError(f"SPECTER2 emitted dim={vecs.shape[1]}, expected {self.dim}")
                matrix[begin:end] = vecs
                progress.update(end - begin)
                if end - last_checkpoint >= self.checkpoint_every or end == corpus.height:
                    matrix.flush()
                    self._save_checkpoint(fingerprint, corpus.height, end)
                    last_checkpoint = end
        progress.close()
        covered = np.ones(corpus.height, dtype=bool)
        return EmbeddingResult(matrix, covered, self.name,
                               f"{self.model}+{self.adapter}:proximity")
