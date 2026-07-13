"""Reconstruct plaintext abstracts from OpenAlex ``abstract_inverted_index``.

OpenAlex stores abstracts as an inverted index ``{word: [positions...]}`` (a legal
workaround — the running text is fully recoverable). This module rebuilds the text and
handles the failure modes that matter: missing index, gaps in positions, and duplicate
words at multiple positions.
"""

from __future__ import annotations

from typing import Optional


def reconstruct_abstract(inverted_index: Optional[dict[str, list[int]]]) -> Optional[str]:
    """Rebuild running text from an OpenAlex inverted index.

    Returns ``None`` if the index is missing/empty so callers can distinguish
    "no abstract" from "empty string". Positions are 0-based; gaps (missing
    positions) are tolerated — words are simply placed at whatever positions exist,
    then concatenated in position order.
    """
    if not inverted_index:
        return None

    # Map position -> word. A word may appear at several positions.
    max_pos = -1
    placed: dict[int, str] = {}
    for word, positions in inverted_index.items():
        for pos in positions:
            placed[pos] = word
            if pos > max_pos:
                max_pos = pos

    if max_pos < 0:
        return None

    # Walk positions in order; skip gaps rather than inserting blanks.
    words = [placed[i] for i in range(max_pos + 1) if i in placed]
    text = " ".join(words).strip()
    return text or None


def embed_text(title: Optional[str], abstract: Optional[str]) -> str:
    """Compose the text fed to the embedding model.

    SPECTER2/SciNCL are trained on ``title [SEP] abstract``. Semantic Scholar's
    SPECTER uses that convention; sentence-transformers models take a single string.
    We join with a period+space which works acceptably for both and degrades
    gracefully to title-only when the abstract is missing.
    """
    title = (title or "").strip()
    abstract = (abstract or "").strip()
    if title and abstract:
        return f"{title}. {abstract}"
    return title or abstract
