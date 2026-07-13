from pipeline.common.abstract import embed_text, reconstruct_abstract


def test_basic_reconstruction():
    # "the quick brown fox" as an inverted index.
    idx = {"the": [0], "quick": [1], "brown": [2], "fox": [3]}
    assert reconstruct_abstract(idx) == "the quick brown fox"


def test_duplicate_words_multiple_positions():
    idx = {"the": [0, 2], "cat": [1], "sat": [3]}
    assert reconstruct_abstract(idx) == "the cat the sat"


def test_out_of_order_index():
    # Insertion order should not matter; position order should win.
    idx = {"world": [1], "hello": [0]}
    assert reconstruct_abstract(idx) == "hello world"


def test_gap_in_positions_is_skipped():
    # Position 1 is missing — we skip it rather than inserting an empty token.
    idx = {"a": [0], "c": [2]}
    assert reconstruct_abstract(idx) == "a c"


def test_missing_and_empty():
    assert reconstruct_abstract(None) is None
    assert reconstruct_abstract({}) is None


def test_embed_text_composition():
    assert embed_text("Title", "Abstract body") == "Title. Abstract body"
    assert embed_text("Title", None) == "Title"
    assert embed_text(None, "Only abstract") == "Only abstract"
    assert embed_text("  ", "  ") == ""
