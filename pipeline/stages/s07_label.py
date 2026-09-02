"""s07: Name graph communities and emit clusters.json + labels.json.

Labels combine two complementary signals:

* discriminative taxonomy values (OpenAlex topics for enriched corpora; primary arXiv
  category codes for the current snapshot), scored by community prevalence and rarity;
* c-TF-IDF phrases mined from representative titles and abstracts in each community.

The labeler runs top-down. Child labels avoid repeating ancestor labels, and siblings use
alternate candidates when their best names collide. This produces specific names at every
zoom band instead of repeating a broad majority subfield.
"""

from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np
import polars as pl
from sklearn.feature_extraction.text import CountVectorizer, ENGLISH_STOP_WORDS

from pipeline.common import log
from pipeline.common.io import read_json, read_npy, write_json
from pipeline.common.schema import Cluster, ClustersDoc, Label, LabelsDoc, LevelBand, TopicRef
from pipeline.config import ARTIFACTS_DIR, CORPUS_ACTIVE, INTERIM_DIR, Config, ensure_dirs, load_config

CORPUS_IN = CORPUS_ACTIVE
VECTORS_IN = INTERIM_DIR / "embeddings.npy"
TILES_IN = INTERIM_DIR / "tiles.json"
CLUSTERS_OUT = ARTIFACTS_DIR / "clusters.json"
LABELS_OUT = ARTIFACTS_DIR / "labels.json"

_EXTRA_STOP = {
    "http", "https", "www", "com", "org", "doi", "arxiv", "abstract", "paper", "papers",
    "propose", "proposed", "method", "methods", "approach", "approaches", "results",
    "result", "using", "used", "use", "based", "novel", "present", "presented", "show",
    "shows", "shown", "study", "studies", "problem", "problems",
    "amp", "gt", "lt", "quot", "apos", "nbsp", "eg", "ie", "et", "al", "fig", "table",
    "pp", "vol", "cc", "icci", "ieee", "acm", "pieceable", "notation", "latex",
    "alttext", "annotation", "application", "caligraphic", "class", "content", "display",
    "encoding", "formula", "inline", "mathml", "mathvariant", "mjx", "ord", "script",
    "semantics", "tex", "texatom", "xlink",
    "content-type", "inline-formula", "mjx-tex-caligraphic", "mjx-texatom-ord", "tex-math",
    "mml", "math", "mrow", "mi", "mo", "mn", "msub", "mfrac", "xmlns",
}
_LABEL_STOP = frozenset(ENGLISH_STOP_WORDS) | _EXTRA_STOP
_GENERIC_PHRASES = {
    "artificial intelligence",
    "computer science",
    "deep learning",
    "large language",
    "machine learning",
    "model",
    "models",
    "natural language",
    "neural network",
    "neural networks",
}
_ACRONYMS = {
    "ai", "cnn", "cpu", "gan", "gnn", "gpt", "gpu", "llm", "ml",
    "nlp", "rl", "sgd", "ssl", "xai",
}
_ACRONYM_DISPLAY = {"gans": "GANs", "llms": "LLMs"}
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_ENTITY_RE = re.compile(r"&[a-z]+;|&#\d+;")
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# A minority of OpenAlex abstracts embed raw MathML/LaTeX-annotation markup for formulas.
# Its tag and attribute names are ordinary words to a tokenizer, so they survive into the
# label vocabulary and win c-TF-IDF by looking rare — e.g. `<mml:mo stretchy="false">`
# produced the region label "Quantum Computing Algorithms and Architecture: Stretchy False".
# Stripping the markup structurally is the fix; enumerating leaked tokens in a stopword
# list is not, because each new attribute or tag name reintroduces the bug.
_MATHML_TAG_RE = re.compile(r"<[^>]*>")
# Attribute pairs (stretchy="false") survive when a tag is unbalanced/truncated.
_ATTR_RE = re.compile(r"\b[a-zA-Z-]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)")
# Namespaced leftovers such as `mml:mi` or a bare `mml:` prefix.
_NS_RE = re.compile(r"\b[a-zA-Z]+:[a-zA-Z]+\b")

_PALETTE = [
    [99, 179, 237], [246, 173, 85], [104, 211, 145], [237, 100, 166],
    [159, 122, 234], [246, 224, 94], [79, 209, 197], [252, 129, 129],
    [144, 205, 244], [183, 148, 244], [104, 211, 145], [237, 137, 54],
]


def _clean(text: str) -> str:
    """Strip URLs, HTML entities, and embedded MathML/LaTeX markup from label vocabulary.

    Order matters: attributes are removed before tags, so that a truncated tag (common when
    an abstract is clipped mid-formula) still loses its attribute names.
    """
    text = _URL_RE.sub(" ", text)
    text = _ATTR_RE.sub(" ", text)
    text = _MATHML_TAG_RE.sub(" ", text)
    text = _NS_RE.sub(" ", text)
    return _ENTITY_RE.sub(" ", text)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _redundant(candidate: str, blocked: set[str]) -> bool:
    candidate_tokens = _tokens(candidate)
    for value in blocked:
        value_tokens = _tokens(value)
        if not candidate_tokens or not value_tokens:
            continue
        if candidate.lower() == value.lower():
            return True
        overlap = len(candidate_tokens & value_tokens) / min(
            len(candidate_tokens),
            len(value_tokens),
        )
        if overlap >= 0.9:
            return True
    return False


def _good_phrase(phrase: str) -> bool:
    words = phrase.lower().split()
    if not words or len(words) != len(set(words)):
        return False
    return phrase.lower() not in _GENERIC_PHRASES


def _display_phrase(phrase: str) -> str:
    words = []
    for word in phrase.split():
        bare = word.strip(".,:;()").lower()
        if bare in _ACRONYM_DISPLAY:
            words.append(_ACRONYM_DISPLAY[bare])
        else:
            words.append(word.upper() if bare in _ACRONYMS else word.title())
    return " ".join(words)


def _representative_members(
    node_idx: list[int],
    vectors: np.ndarray,
    limit: int = 700,
) -> np.ndarray:
    members = np.asarray(node_idx, dtype=np.int32)
    if len(members) <= limit:
        return members
    # Chunked, for the same reason as the planar substrate in s06: `vectors[members]` is fancy
    # indexing, so it COPIES, and the .astype(float64) doubled it again. A band-0 root cell
    # holds hundreds of thousands of members at 3.1M papers, so that copy alone ran to
    # gigabytes per call. Two passes over bounded slices give the identical centroid and
    # ordering without ever materialising the gather.
    chunk = 200_000
    center = np.zeros(vectors.shape[1], dtype=np.float64)
    for start in range(0, len(members), chunk):
        center += vectors[members[start:start + chunk]].sum(axis=0, dtype=np.float64)
    center /= len(members)
    norm = np.linalg.norm(center)
    if norm > 0:
        center = center / norm
    similarity = np.empty(len(members), dtype=np.float64)
    for start in range(0, len(members), chunk):
        stop = start + chunk
        similarity[start:stop] = np.einsum(
            "ij,j->i", vectors[members[start:stop]], center, dtype=np.float64, optimize=True
        )
    return members[np.argsort(-similarity, kind="stable")[:limit]]


def _ctfidf_candidates(
    tiles: list[dict],
    texts: list[str],
    vectors: np.ndarray,
    min_gram: int,
    ngram_max: int,
    exclude: dict[int, set[str]] | None = None,
    top_k: int = 12,
) -> dict[int, list[str]]:
    """Return ranked differentiating phrases for each community."""
    if not tiles:
        return {}
    exclude = exclude or {}
    # Bounded per-document text.
    #
    # CountVectorizer builds the COMPLETE n-gram vocabulary before pruning to max_features, so
    # its peak scales with the raw text handed to it, not with the 60,000 features kept. At
    # 3.1M papers the cells are ~3x larger than at 1M, so many more of them hit the full 700
    # representatives, and this stage was OOM-killed twice (anon-rss 76.3 GB of 78 GB) inside
    # fit_transform. Capping the characters per community bounds that peak independently of
    # corpus size.
    #
    # Truncation is at the END of the concatenation, and members arrive sorted by similarity to
    # the community centroid, so what is dropped is always the least representative material.
    doc_char_cap = 240_000
    docs = []
    for tile in tiles:
        parts: list[str] = []
        used = 0
        for i in _representative_members(tile["node_idx"], vectors):
            part = texts[i]
            parts.append(part)
            used += len(part) + 1
            if used >= doc_char_cap:
                break
        docs.append(" ".join(parts))

    def score(lo: int, hi: int) -> dict[int, list[str]]:
        vectorizer = CountVectorizer(
            ngram_range=(lo, hi),
            stop_words=list(_LABEL_STOP),
            min_df=1,
            max_features=60_000,
            token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z][a-zA-Z\-]+\b",
        )
        try:
            counts = vectorizer.fit_transform(docs).tocsr()
        except ValueError:
            return {}

        vocabulary = np.asarray(vectorizer.get_feature_names_out())
        class_frequency = np.asarray((counts > 0).sum(axis=0)).ravel()
        inverse_frequency = np.log(
            1.0 + counts.shape[0] / np.maximum(class_frequency, 1)
        )
        output: dict[int, list[str]] = {}

        for row, tile in enumerate(tiles):
            start, end = counts.indptr[row], counts.indptr[row + 1]
            columns = counts.indices[start:end]
            frequencies = counts.data[start:end].astype(np.float64)
            total = frequencies.sum()
            if total <= 0:
                continue
            lengths = np.asarray(
                [len(vocabulary[column].split()) for column in columns],
                dtype=np.float64,
            )
            # Longer phrases carry more topic detail; keep the boost modest so one rare
            # four-gram does not outrank a genuinely representative two-gram.
            values = frequencies / total * inverse_frequency[columns] * (
                1.0 + 0.3 * np.maximum(lengths - 2.0, 0.0)
            )
            order = np.argsort(-values, kind="stable")[:max(top_k * 10, 50)]
            blocked = exclude.get(tile["id"], set())
            phrases: list[str] = []
            for position in order:
                phrase = str(vocabulary[columns[position]])
                display = _display_phrase(phrase)
                if not _good_phrase(phrase) or _redundant(display, blocked | set(phrases)):
                    continue
                phrases.append(display)
                if len(phrases) >= top_k:
                    break
            if phrases:
                output[tile["id"]] = phrases
        return output

    multi = score(max(2, min_gram), ngram_max) if ngram_max >= 2 else {}
    unigrams = score(1, 1)
    output: dict[int, list[str]] = {}
    for tile in tiles:
        phrases = list(multi.get(tile["id"], []))
        for phrase in unigrams.get(tile["id"], []):
            if not _redundant(phrase, set(phrases)):
                phrases.append(phrase)
            if len(phrases) >= top_k:
                break
        if phrases:
            output[tile["id"]] = phrases
    return output


def _topic_candidates(
    node_idx: list[int],
    topic_names: list[str | None],
    topic_ids: list[int],
    global_counts: Counter,
    corpus_size: int,
    top_k: int = 8,
) -> list[tuple[str, int]]:
    local = Counter(
        topic_names[index]
        for index in node_idx
        if topic_names[index]
    )
    minimum = max(2, math.ceil(len(node_idx) * 0.01))
    scored: list[tuple[float, str, int]] = []
    for name, count in local.items():
        if count < minimum:
            continue
        corpus_count = global_counts[name]
        specificity = math.log(1.0 + corpus_size / max(corpus_count, 1))
        prevalence = count / len(node_idx)
        score = prevalence * specificity * math.log1p(count)
        topic_id = next(
            topic_ids[index]
            for index in node_idx
            if topic_names[index] == name
        )
        scored.append((score, str(name), int(topic_id)))
    scored.sort(key=lambda value: (-value[0], value[1]))
    return [(name, topic_id) for _, name, topic_id in scored[:top_k]]


def _combined(topic: str, phrase: str) -> str:
    topic_tokens = _tokens(topic)
    phrase_tokens = _tokens(phrase)
    if phrase_tokens <= topic_tokens:
        return topic
    if topic_tokens <= phrase_tokens:
        return phrase
    combined = f"{topic}: {phrase}"
    return combined if len(combined) <= 72 else phrase


# Micro-cluster leaf naming --------------------------------------------------
# c-TF-IDF loses discriminative power for a handful of papers (min_df=1 makes every rare
# n-gram look "distinctive"). For small leaf communities we instead name the group from the
# phrase its members' TITLES literally share, which is concrete and verifiable.

_TITLE_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-]*")
_LEAF_MAX_GROUP = 12  # cells at or below this size prefer a shared-title-phrase leaf name


def _title_ngrams(title: str, lo: int, hi: int) -> set[str]:
    """Content-word n-grams (lo..hi) from one title, stop-words dropped."""
    words = [w for w in _TITLE_TOKEN_RE.findall(_clean(title).lower()) if w not in _LABEL_STOP]
    grams: set[str] = set()
    for n in range(lo, hi + 1):
        for i in range(len(words) - n + 1):
            grams.add(" ".join(words[i:i + n]))
    return grams


def _shared_title_phrase(node_idx: list[int], titles: list[str], cited: list[int]) -> str | None:
    """Longest phrase shared by the most member titles; ties broken by total citations.

    Returns a display-cased phrase (2..6 words) present in >=2 titles, or None when the
    members share no meaningful phrase (then the caller falls back to c-TF-IDF / topic).
    """
    members = node_idx[:60]  # a leaf is tiny; cap defensively
    if len(members) < 2:
        # A singleton: use its own most specific title phrase, if any.
        grams = _title_ngrams(titles[members[0]], 2, 6) if members else set()
        best = max(grams, key=lambda g: len(g.split()), default=None)
        return _display_phrase(best) if best and _good_phrase(best) else None

    # Count how many titles contain each n-gram (document frequency within the group).
    df: Counter = Counter()
    for i in members:
        for g in _title_ngrams(titles[i], 2, 6):
            df[g] += 1
    best_phrase: str | None = None
    best_key: tuple = (1, 0, 0)  # (doc_freq, word_count, summed_citations)
    for phrase, freq in df.items():
        if freq < 2 or not _good_phrase(phrase):
            continue
        cites = sum(
            cited[i] for i in members if phrase in _title_ngrams(titles[i], 2, 6)
        )
        key = (freq, len(phrase.split()), cites)
        if key > best_key:
            best_key = key
            best_phrase = phrase
    return _display_phrase(best_phrase) if best_phrase else None


def _choose_label(
    band: int,
    topics: list[tuple[str, int]],
    phrases: list[str],
    blocked: set[str],
    leaf_phrase: str | None = None,
) -> tuple[str | None, TopicRef | None]:
    """Choose a detailed, non-repeating label and its OpenAlex topic reference.

    ``leaf_phrase`` (a phrase shared by a micro-cluster's titles) is preferred when present,
    so the deepest zoom names a handful of papers by what they literally have in common.
    """
    topic_ref = TopicRef(level="topic", id=topics[0][1]) if topics else None
    topic_names = [name for name, _ in topics]
    combinations = [
        _combined(topic, phrase)
        for topic in topic_names[:3]
        for phrase in phrases[:4]
    ]

    if leaf_phrase:
        # Micro-cluster: shared title phrase first, then the usual detail, then topic.
        candidates = [leaf_phrase] + phrases + combinations + topic_names
    elif band <= 1:
        candidates = combinations + topic_names + phrases
    else:
        candidates = combinations + phrases + topic_names

    seen: set[str] = set()
    for candidate in candidates:
        candidate = _clean(candidate).strip()
        key = candidate.lower()
        if not candidate or key in seen:
            continue
        seen.add(key)
        if not _redundant(candidate, blocked):
            return candidate, topic_ref

    # Coverage is more important than perfect deduplication. If every candidate overlaps
    # an ancestor, retain the most specific available phrase.
    fallback = leaf_phrase or next(iter(phrases or topic_names), None)
    return fallback, topic_ref


def run(cfg: Config | None = None) -> tuple[str, str]:
    cfg = cfg or load_config()
    ensure_dirs()
    log.stage("s07_label")

    # Only the columns this stage reads. The frame has 61, including per-paper author and
    # referenced_works LISTS, and materialising all of them for 3.1M papers is several GB that
    # are never touched here. Freed entirely once the columns below are extracted.
    corpus = pl.read_parquet(
        CORPUS_IN,
        columns=["title", "abstract", "topic_name", "topic_id", "cited_by_count",
                 "subfield_id"],
    )
    n_papers = corpus.height
    vectors = read_npy(VECTORS_IN)
    cells = read_json(TILES_IN)["cells"]

    titles = [title or "" for title in corpus["title"].to_list()]
    if cfg.labels.use_abstract and "abstract" in corpus.columns:
        # Clean here, not just on the chosen candidate: a minority of abstracts carry raw
        # MathML, whose tag/attribute names otherwise enter the c-TF-IDF vocabulary and
        # score as "rare" (this is what produced the "Stretchy False" label).
        abstracts = [_clean(abstract or "") for abstract in corpus["abstract"].to_list()]
        # Titles are concise topic descriptions, so weight them above abstract boilerplate.
        # Titles carry the same MathML as abstracts, so clean them on this path too.
        texts = [
            f"{clean_title}. {clean_title}. {clean_title}. {abstract}"
            for clean_title, abstract in zip((_clean(t) for t in titles), abstracts)
        ]
    else:
        texts = [_clean(title) for title in titles]

    topic_names = corpus["topic_name"].to_list()
    topic_ids = corpus["topic_id"].to_list()
    cited_counts = corpus["cited_by_count"].to_list()
    global_topic_counts = Counter(name for name in topic_names if name)
    subfield_ids = corpus["subfield_id"].to_list()
    # Everything needed is now in plain Python lists; the frame and the intermediate abstract
    # list are dead weight. At 3.1M papers this stage was OOM-killed (anon-rss 76.3 GB of
    # 78 GB) holding the frame, `abstracts` and `texts` alive simultaneously alongside the
    # 9.6 GB vector array, before c-TF-IDF had allocated anything.
    del corpus
    abstracts = None  # noqa: F841 — `texts` already holds the combined strings
    unique_subfields = sorted(set(subfield_ids))
    subfield_color = {
        subfield: _PALETTE[index % len(_PALETTE)]
        for index, subfield in enumerate(unique_subfields)
    }

    clusters: list[Cluster] = []
    labels: list[Label] = []
    cell_label: dict[int, str] = {}
    cell_parent: dict[int, int | None] = {
        cell["id"]: cell.get("parent")
        for cell in cells
    }

    def ancestor_labels(cell_id: int) -> set[str]:
        output: set[str] = set()
        parent = cell_parent.get(cell_id)
        while parent is not None:
            if parent in cell_label:
                output.add(cell_label[parent])
            parent = cell_parent.get(parent)
        return output

    n_bands = max((cell["level"] for cell in cells), default=-1) + 1
    for band in range(n_bands):
        band_cells = [cell for cell in cells if cell["level"] == band]
        if not band_cells:
            continue

        sorted_cells = sorted(band_cells, key=lambda cell: -cell["count"])
        cap = cfg.hierarchy.max_labels_per_level
        label_cells = sorted_cells[:cap]
        keep = {cell["id"] for cell in label_cells}

        # c-TF-IDF is the expensive part (it concatenates up to 700 abstracts per cell).
        # Only the top `cap` cells can be emitted to labels.json, so scoring every hidden
        # cell wastes tens of GB and many minutes at deep bands without changing anything
        # the browser can display. Hidden cells still get lightweight topic/leaf fallback
        # names in clusters.json below.
        excluded = {
            cell["id"]: ancestor_labels(cell["id"])
            for cell in label_cells
        }
        phrase_map = _ctfidf_candidates(
            label_cells,
            texts,
            vectors,
            cfg.labels.ctfidf_min_gram,
            cfg.labels.ngram_max,
            exclude=excluded,
            top_k=cfg.labels.ctfidf_candidates,
        )
        if len(band_cells) > cap:
            # The frontend declutters per viewport, so a global cap only ever removes names
            # the user could have seen by zooming in. Log it rather than dropping silently.
            log.warn(
                f"band {band}: {len(band_cells)} regions but max_labels_per_level={cap} "
                f"— {len(band_cells) - cap} regions will have no label at this band"
            )
        max_count = max((cell["count"] for cell in band_cells), default=1)
        used_at_band: set[str] = set()

        for cell in sorted_cells:
            topics = _topic_candidates(
                cell["node_idx"],
                topic_names,
                topic_ids,
                global_topic_counts,
                n_papers,
            )
            # For small leaf communities, name them by the phrase their titles share.
            leaf_phrase = (
                _shared_title_phrase(cell["node_idx"], titles, cited_counts)
                if cell["count"] <= _LEAF_MAX_GROUP
                else None
            )
            text, topic_ref = _choose_label(
                band,
                topics,
                phrase_map.get(cell["id"], []),
                ancestor_labels(cell["id"]) | used_at_band,
                leaf_phrase=leaf_phrase,
            )
            if not text:
                continue
            text = text.strip()
            cell_label[cell["id"]] = text
            used_at_band.add(text)

            tile_subfields = [subfield_ids[index] for index in cell["node_idx"]]
            majority_subfield = (
                Counter(tile_subfields).most_common(1)[0][0]
                if tile_subfields
                else -1
            )
            color = subfield_color.get(majority_subfield, [160, 160, 160])

            clusters.append(Cluster(
                id=cell["id"],
                level=band,
                parent=cell.get("parent"),
                children=[],
                x=cell["cx"],
                y=cell["cy"],
                count=cell["count"],
                bbox=cell["bbox"],
                color=color,
                label=text,
                topic_ref=topic_ref,
            ))

            if cell["id"] in keep:
                priority = (
                    (n_bands - band) * 1000.0
                    + (cell["count"] / max_count) * 100.0
                )
                labels.append(Label(
                    id=cell["id"],
                    x=cell["cx"],
                    y=cell["cy"],
                    text=text,
                    level=band,
                    priority=round(priority, 2),
                    count=cell["count"],
                ))

    by_id = {cluster.id: cluster for cluster in clusters}
    for cluster in clusters:
        if cluster.parent is not None and cluster.parent in by_id:
            by_id[cluster.parent].children.append(cluster.id)

    step = 1.2
    bands = [
        LevelBand(
            level=band,
            zoom_min=-0.5 + band * step,
            zoom_max=-0.5 + (band + 1) * step + 0.6,
        )
        for band in range(n_bands)
    ]

    write_json(ClustersDoc(levels=bands, clusters=clusters), CLUSTERS_OUT)
    write_json(LabelsDoc(labels=labels), LABELS_OUT)
    log.info(
        f"clusters={len(clusters)} | labels={len(labels)} across {len(bands)} bands"
    )
    for band in range(min(n_bands, 3)):
        sample = [label.text for label in labels if label.level == band][:8]
        log.info(f"band-{band} labels: {sample}")
    return str(CLUSTERS_OUT), str(LABELS_OUT)


if __name__ == "__main__":
    run()
