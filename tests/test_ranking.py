"""ranking.py — relevance, authority, and the blend of the two.

These are the tests that encode *why* the scorers are shaped the way they are.
Most assert a relationship between scores rather than a specific number, so
they stay meaningful if a constant is retuned but start failing the moment a
documented property stops holding.
"""

import math

import pytest

from ranking import (
    AUTHORITY_WEIGHT,
    B,
    K1,
    average_length,
    bm25_idf,
    combine,
    inverse_document_frequency,
    normalize,
    rank,
    relevance_scores,
    score_bm25,
    score_document,
    search,
    term_frequency,
)


class TestInverseDocumentFrequency:
    def test_a_term_in_every_document_is_worthless(self):
        # This is what makes a stopword list unnecessary. log(N/N) = 0, so
        # "the" contributes exactly nothing without anyone listing it.
        index = {"the": {0: 1, 1: 1, 2: 1}}
        assert inverse_document_frequency("the", index, 3) == 0.0

    def test_rarer_terms_score_higher(self):
        index = {"rare": {0: 1}, "common": {0: 1, 1: 1, 2: 1, 3: 1}}
        rare = inverse_document_frequency("rare", index, 5)
        common = inverse_document_frequency("common", index, 5)
        assert rare > common > 0

    def test_unknown_term_is_zero(self):
        assert inverse_document_frequency("absent", {}, 10) == 0.0

    def test_matches_the_documented_formula(self):
        index = {"t": {0: 1}}
        assert inverse_document_frequency("t", index, 6) == pytest.approx(math.log(6))


class TestBm25Idf:
    def test_stays_positive_even_for_a_term_in_every_document(self):
        # The smoothed variant approaches zero without the cliff that log(N/df)
        # falls off, so a universal term still contributes a little.
        index = {"the": {0: 1, 1: 1, 2: 1}}
        assert bm25_idf("the", index, 3) > 0

    def test_rarer_terms_still_score_higher(self):
        index = {"rare": {0: 1}, "common": {0: 1, 1: 1, 2: 1, 3: 1}}
        assert bm25_idf("rare", index, 5) > bm25_idf("common", index, 5)

    def test_unknown_term_is_zero(self):
        assert bm25_idf("absent", {}, 10) == 0.0


class TestTermFrequency:
    def test_is_a_fraction_of_document_length(self, make_corpus):
        documents, index = make_corpus({0: 100}, {0: 5})
        assert term_frequency("einstein", 0, index, documents) == pytest.approx(0.05)

    def test_absent_term_is_zero(self, make_corpus):
        documents, index = make_corpus({0: 100}, {0: 0})
        assert term_frequency("einstein", 0, index, documents) == 0.0


class TestAverageLength:
    def test_is_the_mean(self, make_corpus):
        documents, _ = make_corpus({0: 100, 1: 200, 2: 300}, {})
        assert average_length(documents) == pytest.approx(200.0)

    def test_empty_corpus_is_zero_not_an_error(self):
        assert average_length({}) == 0.0


class TestBm25Properties:
    """The two corrections BM25 makes to TF-IDF, stated as tests."""

    def test_term_counts_saturate(self, make_corpus):
        # Going 1 -> 2 occurrences should buy far more than 19 -> 20. Without
        # saturation both gains would be identical.
        def score_with(count):
            documents, index = make_corpus({0: 200, 1: 200}, {0: count, 1: 1})
            return score_bm25(["einstein"], 0, index, documents, avgdl=200.0)

        early_gain = score_with(2) - score_with(1)
        late_gain = score_with(20) - score_with(19)
        assert early_gain > late_gain * 10

    def test_length_normalization_is_partial(self, make_corpus):
        # b = 0 ignores length entirely; b = 1 divides by it fully, which is
        # TF-IDF's behaviour. The default has to sit strictly between them.
        documents, index = make_corpus({0: 600, 1: 100}, {0: 3, 1: 3})
        avgdl = average_length(documents)

        ignored = score_bm25(["einstein"], 0, index, documents, avgdl, b=0.0)
        partial = score_bm25(["einstein"], 0, index, documents, avgdl, b=B)
        full = score_bm25(["einstein"], 0, index, documents, avgdl, b=1.0)

        assert full < partial < ignored

    def test_k1_of_zero_makes_only_presence_matter(self, make_corpus):
        documents, index = make_corpus({0: 200, 1: 200}, {0: 1, 1: 50})
        one = score_bm25(["einstein"], 0, index, documents, 200.0, k1=0.0)
        many = score_bm25(["einstein"], 1, index, documents, 200.0, k1=0.0)
        assert one == pytest.approx(many)

    def test_absent_term_contributes_nothing(self, make_corpus):
        documents, index = make_corpus({0: 200, 1: 200}, {0: 0, 1: 1})
        assert score_bm25(["einstein"], 0, index, documents, 200.0) == 0.0

    def test_empty_corpus_does_not_divide_by_zero(self, make_corpus):
        documents, index = make_corpus({0: 10}, {0: 1})
        assert score_bm25(["einstein"], 0, index, documents, avgdl=0.0) >= 0.0

    def test_defaults_are_the_documented_values(self):
        assert (K1, B) == (1.5, 0.75)


class TestTheRegressionBm25Fixes:
    """The exact failure that motivated switching the default scorer.

    On the real crawl, a 53-token tag page mentioning "einstein" once was
    beating the 650-token biography that mentions him twelve times. These
    numbers are that scenario, and the point of the test is that the two
    scorers disagree about it.
    """

    @pytest.fixture
    def corpus(self, make_corpus):
        lengths = {0: 650, 1: 53}
        counts = {0: 12, 1: 1}
        # Filler so the term is rare enough to have positive IDF and the
        # corpus average is realistic.
        lengths.update({i: 200 for i in range(2, 40)})
        return make_corpus(lengths, counts)

    def test_tfidf_prefers_the_short_stub(self, corpus):
        documents, index = corpus
        biography = score_document(["einstein"], 0, index, documents)
        stub = score_document(["einstein"], 1, index, documents)
        assert stub > biography

    def test_bm25_prefers_the_real_page(self, corpus):
        documents, index = corpus
        avgdl = average_length(documents)
        biography = score_bm25(["einstein"], 0, index, documents, avgdl)
        stub = score_bm25(["einstein"], 1, index, documents, avgdl)
        assert biography > stub


class TestNormalize:
    def test_largest_becomes_one(self):
        assert normalize({0: 2.0, 1: 4.0}) == {0: 0.5, 1: 1.0}

    def test_all_zero_scores_do_not_divide_by_zero(self):
        assert normalize({0: 0.0, 1: 0.0}) == {0: 0.0, 1: 0.0}

    def test_empty_input(self):
        assert normalize({}) == {}


class TestCombine:
    def test_zero_weight_is_pure_relevance(self):
        relevance = {0: 1.0, 1: 0.5}
        authority = {0: 0.01, 1: 0.99}
        combined = combine(relevance, authority, authority_weight=0.0)
        assert combined[0] > combined[1]

    def test_full_weight_is_pure_authority(self):
        relevance = {0: 1.0, 1: 0.5}
        authority = {0: 0.01, 1: 0.99}
        combined = combine(relevance, authority, authority_weight=1.0)
        assert combined[1] > combined[0]

    def test_missing_authority_is_treated_as_zero(self):
        combined = combine({0: 1.0, 1: 1.0}, {0: 0.5}, authority_weight=0.5)
        assert combined[0] > combined[1]

    def test_puts_both_signals_on_the_same_scale_first(self):
        # Relevance and authority naturally live on different scales. Whichever
        # is numerically bigger must not win by accident.
        relevance = {0: 100.0, 1: 50.0}
        authority = {0: 0.001, 1: 0.002}
        combined = combine(relevance, authority, authority_weight=0.99)
        assert combined[1] > combined[0]

    def test_default_weight_is_the_documented_value(self):
        assert AUTHORITY_WEIGHT == 0.2


class TestRelevanceScores:
    def test_method_selects_the_scorer(self, make_corpus):
        documents, index = make_corpus({0: 650, 1: 53}, {0: 12, 1: 1})
        candidates = {0, 1}
        tfidf = relevance_scores(["einstein"], candidates, index, documents, "tfidf")
        bm25 = relevance_scores(["einstein"], candidates, index, documents, "bm25")
        assert tfidf != bm25

    def test_avgdl_is_computed_when_not_supplied(self, make_corpus):
        documents, index = make_corpus({0: 650, 1: 53}, {0: 12, 1: 1})
        supplied = relevance_scores(
            ["einstein"], {0, 1}, index, documents, "bm25", avgdl=average_length(documents)
        )
        derived = relevance_scores(["einstein"], {0, 1}, index, documents, "bm25")
        assert supplied == derived


class TestRank:
    def test_orders_best_first(self, make_corpus):
        documents, index = make_corpus({0: 200, 1: 200}, {0: 1, 1: 9})
        ranked = rank(["einstein"], {0, 1}, index, documents)
        assert [doc_id for doc_id, _ in ranked] == [1, 0]

    def test_ties_break_on_document_id(self, make_corpus):
        # Documented behaviour: identical documents must come back in a stable,
        # predictable order rather than whatever the set happened to yield.
        documents, index = make_corpus({0: 200, 1: 200, 2: 200}, {0: 5, 1: 5, 2: 5})
        ranked = rank(["einstein"], {0, 1, 2}, index, documents)
        assert [doc_id for doc_id, _ in ranked] == [0, 1, 2]

    def test_limit_truncates(self, make_corpus):
        documents, index = make_corpus({0: 200, 1: 200, 2: 200}, {0: 3, 1: 2, 2: 1})
        assert len(rank(["einstein"], {0, 1, 2}, index, documents, limit=2)) == 2

    def test_without_authority_it_is_pure_relevance(self, make_corpus):
        documents, index = make_corpus({0: 200, 1: 200}, {0: 1, 1: 9})
        relevance_only = rank(["einstein"], {0, 1}, index, documents)
        with_authority = rank(
            ["einstein"], {0, 1}, index, documents, authority={0: 1.0, 1: 0.0}
        )
        assert relevance_only[0][0] == 1
        assert with_authority != relevance_only

    def test_no_candidates_yields_nothing(self, make_corpus):
        documents, index = make_corpus({0: 200}, {0: 1})
        assert rank(["einstein"], set(), index, documents) == []


class TestSearch:
    def test_runs_the_whole_online_path(self, index, documents):
        results = search("Python!", index, documents)
        assert results
        assert all(isinstance(doc_id, int) for doc_id, _ in results)

    def test_query_that_matches_nothing_returns_nothing(self, index, documents):
        assert search("zzzznotpresent", index, documents) == []

    def test_punctuation_only_query_returns_nothing(self, index, documents):
        assert search("!!!", index, documents) == []
