"""BM25 keeps pace with keyword sums on issue-length queries (#232)."""

from mimry.search import LONG_QUERY_TERMS, bm25_scale


def test_short_queries_keep_the_bm25_boost_unchanged():
    for count in (0, 1, 12, LONG_QUERY_TERMS):
        assert bm25_scale(count) == 1.0


def test_long_queries_scale_bm25_four_times_per_further_block():
    assert bm25_scale(LONG_QUERY_TERMS * 2) == 5.0
    assert bm25_scale(LONG_QUERY_TERMS * 3) == 9.0
    assert bm25_scale(LONG_QUERY_TERMS + 6) == 2.0
