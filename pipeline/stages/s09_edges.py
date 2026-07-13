"""s09: Build the directed intra-corpus citation edge list.

Each paper's ``referenced_works`` gives its outgoing citations (paper -> referenced work).
We keep only edges where BOTH endpoints are in our corpus (so the frontend can resolve
both to node_ids and draw the arc). Cross-corpus references are dropped for the MVP.

Emits:
    data/interim/edges.npz  (src [E] int32 citing, dst [E] int32 cited)
"""

from __future__ import annotations

import numpy as np
import polars as pl

from pipeline.common import log
from pipeline.config import INTERIM_DIR, Config, ensure_dirs, load_config

CORPUS_IN = INTERIM_DIR / "corpus.parquet"
OUT = INTERIM_DIR / "edges.npz"


def run(cfg: Config | None = None) -> str:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s09_edges")

    corpus = pl.read_parquet(CORPUS_IN)
    # Map OpenAlex paper_id -> node_id.
    id_to_node = {pid: nid for pid, nid in
                  zip(corpus["paper_id"].to_list(), corpus["node_id"].to_list())}

    src_list: list[int] = []
    dst_list: list[int] = []
    refs_col = corpus["referenced_works"].to_list()
    nodes_col = corpus["node_id"].to_list()
    for nid, refs in zip(nodes_col, refs_col):
        for ref in refs or []:
            dst = id_to_node.get(ref)
            if dst is not None and dst != nid:
                src_list.append(nid)
                dst_list.append(dst)

    src = np.asarray(src_list, dtype=np.int32)
    dst = np.asarray(dst_list, dtype=np.int32)
    np.savez(OUT, src=src, dst=dst)

    n = corpus.height
    n_with_out = len(set(src_list))
    log.info(f"intra-corpus edges: {len(src)} | {n_with_out}/{n} papers cite within corpus")
    log.info(f"wrote -> {OUT}")
    return str(OUT)


if __name__ == "__main__":
    run()
