from pipeline.stages.s07_label import (
    _LABEL_STOP,
    _choose_label,
    _display_phrase,
    _shared_title_phrase,
)


def test_label_combines_topic_with_specific_community_phrase():
    label, topic_ref = _choose_label(
        band=2,
        topics=[("World Models", 42)],
        phrases=["Action-Conditioned Dynamics"],
        blocked=set(),
    )

    assert label == "World Models: Action-Conditioned Dynamics"
    assert topic_ref is not None
    assert topic_ref.id == 42


def test_markup_tokens_and_acronyms_are_normalized():
    assert {"inline-formula", "content-type", "tex-math", "mathml"} <= _LABEL_STOP
    assert _display_phrase("gnn training for llms") == "GNN Training For LLMs"


def test_leaf_phrase_names_a_micro_cluster_by_shared_title_words():
    titles = [
        "Diffusion Policy for Bimanual Manipulation of Deformable Objects",
        "Learning Diffusion Policy for Bimanual Manipulation from Demonstrations",
        "A Survey of Reinforcement Learning",  # unrelated -> should not steer the name
    ]
    phrase = _shared_title_phrase([0, 1, 2], titles, cited=[10, 5, 100])
    assert phrase is not None
    # The phrase shared by the two related titles should surface, display-cased.
    assert "Diffusion Policy" in phrase and "Bimanual Manipulation" in phrase


def test_leaf_phrase_prefers_the_shared_phrase_as_label():
    label, _ = _choose_label(
        band=9,
        topics=[("Robotics", 7)],
        phrases=["Some Generic Phrase"],
        blocked=set(),
        leaf_phrase="Legged Locomotion Sim-To-Real",
    )
    assert label == "Legged Locomotion Sim-To-Real"


def test_leaf_phrase_none_when_no_shared_content():
    titles = ["Quantum Error Correction Codes", "Federated Learning Privacy Bounds"]
    assert _shared_title_phrase([0, 1], titles, cited=[3, 4]) is None


def test_clean_strips_mathml_so_attribute_names_cannot_become_labels():
    """Regression: `<mml:mo stretchy="false">` named a 7,408-paper region "Stretchy False".

    OpenAlex abstracts embed raw MathML for formulas. Its tag and attribute names are
    ordinary words to a tokenizer and score as "rare" in c-TF-IDF, so they win the label.
    Stripping must be structural — a stopword list breaks on the next new attribute name.
    """
    from pipeline.stages.s07_label import _clean

    text = (
        'complexity of <mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML">'
        '<mml:mi>O</mml:mi><mml:mo stretchy="false">(</mml:mo></mml:math> '
        "for sparse tensor decomposition"
    )
    cleaned = _clean(text).lower()
    for leaked in ("stretchy", "false", "mml", "xmlns", "math/mathml"):
        assert leaked not in cleaned, f"{leaked!r} survived cleaning: {cleaned!r}"
    # The real content must survive.
    assert "sparse tensor decomposition" in cleaned
    assert "complexity" in cleaned

    # A truncated tag (abstract clipped mid-formula) must still lose its attributes.
    assert "stretchy" not in _clean('graph coloring <mml:mo stretchy="false" fence="fal').lower()
    # Ordinary text must pass through untouched.
    assert (
        _clean("Action-conditioned world models for robot manipulation")
        == "Action-conditioned world models for robot manipulation"
    )
