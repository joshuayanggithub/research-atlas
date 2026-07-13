"""s07: Label the hierarchy tiles and emit clusters.json + labels.json.

Each tile gets a label two ways; we pick per band:
  - **OpenAlex topic majority**: the most common OpenAlex label among a tile's papers at
    the taxonomy level matching the band (coarse band -> subfield e.g. "Artificial
    Intelligence"; finer bands -> topic e.g. "Self-Supervised Learning"). Curated, clean.
  - **c-TF-IDF n-gram**: treat the tile's texts as one document, score n-grams against all
    tiles at that band; the top n-gram is the differentiating phrase (e.g. "world models").
    This gives the finest band its specific vocabulary the taxonomy can't.

Band -> taxonomy level mapping (our corpus is all CS field 17, so field is constant and
we start at subfield): band0=subfield, band1=topic, band2=topic+ctfidf, band3=ctfidf.

Emits:
    data/artifacts/clusters.json  (schema.ClustersDoc)
    data/artifacts/labels.json    (schema.LabelsDoc)
"""

from __future__ import annotations

from collections import Counter

import numpy as np
import polars as pl
from sklearn.feature_extraction.text import CountVectorizer

from pipeline.common import log
from pipeline.common.io import read_json, write_json
from pipeline.common.schema import Cluster, ClustersDoc, Label, LabelsDoc, LevelBand, TopicRef
from pipeline.config import ARTIFACTS_DIR, CORPUS_ACTIVE, INTERIM_DIR, Config, ensure_dirs, load_config

CORPUS_IN = CORPUS_ACTIVE
TILES_IN = INTERIM_DIR / "tiles.json"
CLUSTERS_OUT = ARTIFACTS_DIR / "clusters.json"
LABELS_OUT = ARTIFACTS_DIR / "labels.json"

# Which taxonomy level names a band prefers. "ctfidf" means use the c-TF-IDF phrase.
BAND_TAXONOMY = {0: "subfield", 1: "topic", 2: "topic", 3: "ctfidf"}


def _majority_topic(node_idx: list[int], names: list, band_level: str):
    """Return (label, TopicRef|None) for the majority taxonomy name in a tile."""
    col_name = f"{band_level}_name"
    col_id = f"{band_level}_id"
    vals = [(names[col_name][i], names[col_id][i]) for i in node_idx
            if names[col_name][i]]
    if not vals:
        return None, None
    label, _ = Counter(v[0] for v in vals).most_common(1)[0]
    # Find the id that goes with the winning label.
    tid = next((v[1] for v in vals if v[0] == label), -1)
    return label, TopicRef(level=band_level, id=int(tid)) if tid >= 0 else None


def _ctfidf_labels(tiles: list[dict], texts: list[str], top_n: int, ngram_max: int) -> dict:
    """Compute a differentiating n-gram per tile via class-based TF-IDF.

    Returns {tile_id: phrase}. Each tile is one "class document" = concatenation of its
    members' texts; c-TF-IDF finds the n-gram most specific to that tile vs the rest.
    """
    if not tiles:
        return {}
    docs = []
    for t in tiles:
        # Cap per-tile text to keep the vectorizer fast on big cells.
        idx = t["node_idx"][:400]
        docs.append(" ".join(texts[i] for i in idx))

    vec = CountVectorizer(
        ngram_range=(1, ngram_max),
        stop_words="english",
        min_df=1,
        max_features=20000,
        token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z\-]+\b",
    )
    try:
        counts = vec.fit_transform(docs)  # [n_tiles, n_terms]
    except ValueError:
        return {}
    vocab = np.array(vec.get_feature_names_out())

    tf = counts.toarray().astype(np.float64)
    tf_sum = tf.sum(axis=1, keepdims=True)
    tf_sum[tf_sum == 0] = 1.0
    tf_norm = tf / tf_sum
    # class-based IDF: log(1 + avg_count_across_classes / count_in_class)
    n_classes = tf.shape[0]
    class_freq = (tf > 0).sum(axis=0)  # in how many tiles each term appears
    idf = np.log(1.0 + n_classes / np.maximum(class_freq, 1))
    ctfidf = tf_norm * idf

    out = {}
    for row, t in enumerate(tiles):
        top = np.argsort(-ctfidf[row])[:top_n]
        phrases = [vocab[j] for j in top if ctfidf[row, j] > 0]
        if phrases:
            out[t["id"]] = phrases[0]
    return out


# A small palette (RGB) cycled per subfield for point/cluster coloring.
_PALETTE = [
    [99, 179, 237], [246, 173, 85], [104, 211, 145], [237, 100, 166],
    [159, 122, 234], [246, 224, 94], [79, 209, 197], [252, 129, 129],
    [144, 205, 244], [183, 148, 244], [104, 211, 145], [237, 137, 54],
]


def run(cfg: Config | None = None) -> tuple[str, str]:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s07_label")

    corpus = pl.read_parquet(CORPUS_IN)
    tiles_doc = read_json(TILES_IN)
    cells = tiles_doc["cells"]

    texts = corpus["title"].to_list()  # titles are enough for label vocabulary + fast
    names = {
        "subfield_name": corpus["subfield_name"].to_list(),
        "subfield_id": corpus["subfield_id"].to_list(),
        "topic_name": corpus["topic_name"].to_list(),
        "topic_id": corpus["topic_id"].to_list(),
    }
    subfield_ids = corpus["subfield_id"].to_list()

    # Assign a color per subfield id (stable).
    uniq_sub = sorted(set(subfield_ids))
    sub_color = {sid: _PALETTE[i % len(_PALETTE)] for i, sid in enumerate(uniq_sub)}

    clusters: list[Cluster] = []
    labels: list[Label] = []

    for band in range(cfg.hierarchy.max_depth):
        band_cells = [c for c in cells if c["level"] == band]
        band_level = BAND_TAXONOMY.get(band, "ctfidf")

        # Precompute c-TF-IDF for this band if any tile needs it.
        need_ctfidf = band_level == "ctfidf" or band >= 2
        ctfidf_map = (_ctfidf_labels(band_cells, texts, cfg.labels.ctfidf_top_n,
                                     cfg.labels.ngram_max) if need_ctfidf else {})

        # Rank cells by count; keep the top max_labels_per_level as label candidates.
        band_cells_sorted = sorted(band_cells, key=lambda c: -c["count"])
        keep = set(c["id"] for c in band_cells_sorted[:cfg.hierarchy.max_labels_per_level])

        max_count = max((c["count"] for c in band_cells), default=1)

        for c in band_cells:
            # Determine label text.
            topic_ref = None
            if band_level == "ctfidf":
                text = ctfidf_map.get(c["id"])
            else:
                text, topic_ref = _majority_topic(c["node_idx"], names, band_level)
                if band >= 2:
                    # Refine with the c-TF-IDF phrase when available (more specific).
                    text = ctfidf_map.get(c["id"], text)
            if not text:
                continue
            text = text.strip().title() if len(text) < 40 else text.strip()

            # Color: majority subfield color of the tile.
            tile_subs = [subfield_ids[i] for i in c["node_idx"]]
            maj_sub = Counter(tile_subs).most_common(1)[0][0] if tile_subs else -1
            color = sub_color.get(maj_sub, [160, 160, 160])

            clusters.append(Cluster(
                id=c["id"], level=band, parent=c.get("parent"), children=[],
                x=c["cx"], y=c["cy"], count=c["count"], bbox=c["bbox"],
                color=color, label=text, topic_ref=topic_ref,
            ))

            if c["id"] in keep:
                # priority: coarser bands + larger tiles win scarce screen space.
                priority = (cfg.hierarchy.max_depth - band) * 1000.0 + \
                    (c["count"] / max_count) * 100.0
                labels.append(Label(
                    id=c["id"], x=c["cx"], y=c["cy"], text=text,
                    level=band, priority=round(priority, 2), count=c["count"],
                ))

    # Fill children links from parent pointers.
    by_id = {c.id: c for c in clusters}
    for c in clusters:
        if c.parent is not None and c.parent in by_id:
            by_id[c.parent].children.append(c.id)

    # Zoom bands: assign each band a viewport-zoom window (frontend maps live zoom -> band).
    # Orthographic zoom in deck.gl is log2 scale; we hand out evenly spaced windows.
    bands = []
    for band in range(cfg.hierarchy.max_depth):
        bands.append(LevelBand(level=band,
                               zoom_min=-4.0 + band * 2.0,
                               zoom_max=-4.0 + (band + 1) * 2.0 + 0.5))

    write_json(ClustersDoc(levels=bands, clusters=clusters), CLUSTERS_OUT)
    write_json(LabelsDoc(labels=labels), LABELS_OUT)
    log.info(f"clusters={len(clusters)} | labels={len(labels)} across {len(bands)} bands")
    # Show a few band-0 labels as a sanity check.
    b0 = [lb.text for lb in labels if lb.level == 0][:8]
    log.info(f"band-0 labels: {b0}")
    return str(CLUSTERS_OUT), str(LABELS_OUT)


if __name__ == "__main__":
    run()
