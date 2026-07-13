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

import re
from collections import Counter

import numpy as np
import polars as pl
from sklearn.feature_extraction.text import CountVectorizer, ENGLISH_STOP_WORDS

from pipeline.common import log
from pipeline.common.io import read_json, write_json
from pipeline.common.schema import Cluster, ClustersDoc, Label, LabelsDoc, LevelBand, TopicRef
from pipeline.config import ARTIFACTS_DIR, CORPUS_ACTIVE, INTERIM_DIR, Config, ensure_dirs, load_config

# Boilerplate/noise words common in abstracts that make useless labels. Combined with
# sklearn's English stopwords. (HTML entities like &gt; and URLs are stripped in _clean.)
_EXTRA_STOP = {
    "http", "https", "www", "com", "org", "doi", "arxiv", "abstract", "paper", "papers",
    "propose", "proposed", "method", "methods", "approach", "approaches", "results",
    "result", "using", "used", "use", "based", "novel", "present", "presented", "show",
    "shows", "shown", "study", "studies", "problem", "problems", "model", "models",
    "amp", "gt", "lt", "quot", "apos", "nbsp", "eg", "ie", "et", "al", "fig", "table",
    "pp", "vol", "cc", "icci", "ieee", "acm", "pieceable",
    "mml", "math", "mrow", "mi", "mo", "mn", "msub", "mfrac", "xmlns",
}


def _good_phrase(phrase: str) -> bool:
    """Reject degenerate phrases: repeated adjacent words ("federated learning federated")
    or a phrase that is just one word repeated."""
    words = phrase.lower().split()
    if len(words) != len(set(words)):
        return False
    return True
_LABEL_STOP = frozenset(ENGLISH_STOP_WORDS) | _EXTRA_STOP

_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_ENTITY_RE = re.compile(r"&[a-z]+;|&#\d+;")


def _clean(text: str) -> str:
    """Strip URLs and HTML entities that leak into OpenAlex abstracts."""
    text = _URL_RE.sub(" ", text)
    text = _ENTITY_RE.sub(" ", text)
    return text

CORPUS_IN = CORPUS_ACTIVE
TILES_IN = INTERIM_DIR / "tiles.json"
CLUSTERS_OUT = ARTIFACTS_DIR / "clusters.json"
LABELS_OUT = ARTIFACTS_DIR / "labels.json"

# Which taxonomy level names a band prefers. "ctfidf" means use the c-TF-IDF phrase mined
# from the region's papers. Coarse bands borrow OpenAlex's curated names (clean, legible);
# deeper bands switch to c-TF-IDF phrases, which resolve finer than OpenAlex's ~4.5k topics.
# Any band index beyond the last key falls back to "ctfidf".
BAND_TAXONOMY = {0: "subfield", 1: "topic", 2: "topic"}


def _band_level(band: int) -> str:
    return BAND_TAXONOMY.get(band, "ctfidf")


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


def _ctfidf_labels(tiles: list[dict], texts: list[str], min_gram: int, ngram_max: int,
                   exclude: dict[int, set[str]] | None = None, top_k: int = 6) -> dict:
    """Compute a differentiating phrase per tile via class-based TF-IDF.

    Returns {tile_id: phrase}. Each tile is one "class document" = concatenation of its
    members' texts; c-TF-IDF finds the phrase most specific to that tile vs the rest.
    We favor multi-word phrases (min_gram>=2) so labels read as topics ("world models",
    "graph neural networks") rather than bare words ("model", "graph").

    `exclude[tile_id]` is a set of phrases (typically the tile's ancestors' labels) to skip,
    so a child region gets a MORE SPECIFIC phrase than its parent instead of repeating it.
    """
    if not tiles:
        return {}
    exclude = exclude or {}
    docs = [" ".join(texts[i] for i in t["node_idx"][:600]) for t in tiles]

    def _score(lo: int) -> dict:
        vec = CountVectorizer(
            ngram_range=(lo, ngram_max),
            stop_words=list(_LABEL_STOP),
            min_df=1,
            max_features=40000,
            token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z][a-zA-Z\-]+\b",  # words >= 3 chars
        )
        try:
            counts = vec.fit_transform(docs)
        except ValueError:
            return {}
        vocab = np.array(vec.get_feature_names_out())
        tf = counts.toarray().astype(np.float64)
        tf_sum = tf.sum(axis=1, keepdims=True)
        tf_sum[tf_sum == 0] = 1.0
        tf_norm = tf / tf_sum
        n_classes = tf.shape[0]
        class_freq = (tf > 0).sum(axis=0)
        idf = np.log(1.0 + n_classes / np.maximum(class_freq, 1))
        ctfidf = tf_norm * idf
        out = {}
        for row, t in enumerate(tiles):
            banned = exclude.get(t["id"], set())
            # Walk the top candidates; take the best phrase not banned by an ancestor.
            order = np.argsort(-ctfidf[row])[:top_k]
            for j in order:
                if ctfidf[row, j] <= 0:
                    break
                phrase = str(vocab[j])
                if not _good_phrase(phrase):
                    continue
                if phrase.title() not in banned:
                    out[t["id"]] = phrase
                    break
        return out

    phrases = _score(max(min_gram, 2)) if ngram_max >= 2 else {}
    unigrams = _score(1)
    return {t["id"]: phrases.get(t["id"]) or unigrams.get(t["id"]) for t in tiles
            if phrases.get(t["id"]) or unigrams.get(t["id"])}


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

    # Label vocabulary source: title (+ abstract if enabled) gives richer, finer phrases.
    if cfg.labels.use_abstract and "abstract" in corpus.columns:
        titles = corpus["title"].to_list()
        abstracts = corpus["abstract"].to_list()
        texts = [f"{t or ''}. {a or ''}" for t, a in zip(titles, abstracts)]
    else:
        texts = corpus["title"].to_list()
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
    cell_label: dict[int, str] = {}   # cell id -> assigned label (for child exclusion)
    cell_parent: dict[int, int | None] = {c["id"]: c.get("parent") for c in cells}

    def _ancestor_labels(cell_id: int) -> set[str]:
        """Labels of this cell's ancestors, so a child avoids repeating them."""
        out: set[str] = set()
        p = cell_parent.get(cell_id)
        while p is not None:
            if p in cell_label:
                out.add(cell_label[p])
            p = cell_parent.get(p)
        return out

    n_bands = max((c["level"] for c in cells), default=-1) + 1
    for band in range(n_bands):  # top-down so ancestor labels exist before children
        band_cells = [c for c in cells if c["level"] == band]
        if not band_cells:
            continue
        band_level = _band_level(band)

        # Precompute c-TF-IDF for this band if any tile needs it. Pass ancestor labels so
        # children get a MORE SPECIFIC phrase than their parent (avoids "Graph Neural" at
        # every zoom depth).
        need_ctfidf = band_level == "ctfidf" or band >= 2
        exclude = {c["id"]: _ancestor_labels(c["id"]) for c in band_cells}
        ctfidf_map = (_ctfidf_labels(band_cells, texts, cfg.labels.ctfidf_min_gram,
                                     cfg.labels.ngram_max, exclude=exclude)
                      if need_ctfidf else {})

        band_cells_sorted = sorted(band_cells, key=lambda c: -c["count"])
        keep = set(c["id"] for c in band_cells_sorted[:cfg.hierarchy.max_labels_per_level])
        max_count = max((c["count"] for c in band_cells), default=1)

        for c in band_cells:
            topic_ref = None
            if band_level == "ctfidf":
                text = ctfidf_map.get(c["id"])
            else:
                text, topic_ref = _majority_topic(c["node_idx"], names, band_level)
                if band >= 2:
                    text = ctfidf_map.get(c["id"], text)
            if not text:
                continue
            text = _clean(text).strip()
            text = text.title() if len(text) < 40 else text
            if not text:
                continue
            cell_label[c["id"]] = text

            tile_subs = [subfield_ids[i] for i in c["node_idx"]]
            maj_sub = Counter(tile_subs).most_common(1)[0][0] if tile_subs else -1
            color = sub_color.get(maj_sub, [160, 160, 160])

            clusters.append(Cluster(
                id=c["id"], level=band, parent=c.get("parent"), children=[],
                x=c["cx"], y=c["cy"], count=c["count"], bbox=c["bbox"],
                color=color, label=text, topic_ref=topic_ref,
            ))

            if c["id"] in keep:
                priority = (n_bands - band) * 1000.0 + \
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

    # Zoom bands as OFFSETS from the frontend's runtime "fit" zoom (the zoom at which the
    # whole map fills the viewport). The frontend computes fitZoom from the actual viewport
    # size + coordinate bounds and adds these offsets, so bands are correct at any window
    # size. Band 0 starts slightly before fit; each deeper band is +1.5 zoom (2.8x closer),
    # with a 0.5 overlap so bands cross-fade.
    STEP = 1.2
    bands = []
    for band in range(n_bands):
        bands.append(LevelBand(level=band,
                               zoom_min=-0.5 + band * STEP,
                               zoom_max=-0.5 + (band + 1) * STEP + 0.6))

    write_json(ClustersDoc(levels=bands, clusters=clusters), CLUSTERS_OUT)
    write_json(LabelsDoc(labels=labels), LABELS_OUT)
    log.info(f"clusters={len(clusters)} | labels={len(labels)} across {len(bands)} bands")
    # Show a few band-0 labels as a sanity check.
    b0 = [lb.text for lb in labels if lb.level == 0][:8]
    log.info(f"band-0 labels: {b0}")
    return str(CLUSTERS_OUT), str(LABELS_OUT)


if __name__ == "__main__":
    run()
