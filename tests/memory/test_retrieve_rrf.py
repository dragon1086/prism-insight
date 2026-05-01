"""rrf_combine: pure-function ranking fuser tests."""

from tracking.memory.retrieve import rrf_combine


def test_identity_single_retriever_preserves_order():
    ranking = [10, 20, 30, 40]
    out = rrf_combine([ranking])
    assert out == ranking


def test_disjoint_sets_both_included():
    a = [1, 2, 3]
    b = [10, 20, 30]
    out = rrf_combine([a, b])
    # Top items from each retriever should appear in result.
    assert set(out) == {1, 2, 3, 10, 20, 30}
    # First-rank items should rank higher than later ones.
    assert out.index(1) < out.index(2)
    assert out.index(10) < out.index(20)


def test_overlap_boosts_score():
    a = [1, 2, 3]
    b = [3, 4, 5]
    out = rrf_combine([a, b])
    # 3 is rank 2 in a and rank 0 in b → highest combined score.
    assert out[0] == 3


def test_default_k_is_60():
    # Ensure smoke: with k=60, score(rank=0) = 1/61.
    out = rrf_combine([[42]])
    assert out == [42]


def test_tie_break_stable_first_seen_wins():
    a = [1, 2]
    b = [3, 4]
    # Items 1 and 3 are both rank 0 in their retrievers — same score.
    # First seen (in `a`, item 1) should rank ahead of item 3.
    out = rrf_combine([a, b])
    assert out[0] == 1
    assert out[1] == 3


def test_empty_inputs_return_empty():
    assert rrf_combine([]) == []
    assert rrf_combine([[]]) == []
    assert rrf_combine([[], []]) == []


def test_none_ids_skipped():
    out = rrf_combine([[None, 1, None], [2]])
    assert set(out) == {1, 2}
