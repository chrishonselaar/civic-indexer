"""Pure retrieval math: RRF fusion and doc-vector pooling (no model, no DB)."""

import math

from engine.retrieval import l2_normalized_mean, rrf_fuse


def test_rrf_doc_in_both_channels_beats_single_channel_top():
    fused = rrf_fuse([["a", "b", "c"], ["b", "d", "a"]])
    ids = [doc_id for doc_id, _ in fused]
    # "a" (ranks 1+3) and "b" (ranks 2+1) both appear twice; b's rank sum wins.
    assert ids[0] == "b"
    assert ids[1] == "a"
    # Single-channel docs trail dual-channel ones.
    assert ids.index("c") > ids.index("a")
    assert set(ids) == {"a", "b", "c", "d"}


def test_rrf_scores_match_definition():
    fused = dict(rrf_fuse([["x"], ["x"]], k=60))
    assert math.isclose(fused["x"], 2 / 61)


def test_rrf_tie_broken_by_first_list_order():
    fused = rrf_fuse([["a", "b"], ["b", "a"]])
    assert [doc_id for doc_id, _ in fused] == ["a", "b"]


def test_l2_normalized_mean_is_unit_length():
    pooled = l2_normalized_mean([[1.0, 0.0], [0.0, 1.0]])
    assert math.isclose(math.sqrt(sum(x * x for x in pooled)), 1.0)
    assert math.isclose(pooled[0], pooled[1])


def test_l2_normalized_mean_single_vector_identity_direction():
    pooled = l2_normalized_mean([[3.0, 4.0]])
    assert math.isclose(pooled[0], 0.6) and math.isclose(pooled[1], 0.8)
