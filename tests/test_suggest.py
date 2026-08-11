"""suggest.py — example queries derived from the index itself."""

from suggest import MIN_DOCUMENT_FREQ, MIN_TERM_LENGTH, suggest_queries


class TestSuggestQueries:
    def test_suggestions_come_from_the_index(self, index, documents):
        for term in suggest_queries(index, documents):
            assert term in index

    def test_every_suggestion_actually_returns_results(self, index, documents):
        # A suggestion that matches nothing is a dead end on the landing page.
        for term in suggest_queries(index, documents):
            assert len(index[term]) >= MIN_DOCUMENT_FREQ

    def test_skips_terms_shorter_than_the_minimum(self, index, documents):
        assert all(len(t) >= MIN_TERM_LENGTH for t in suggest_queries(index, documents))

    def test_skips_pure_numbers(self, documents):
        index = {"2024": {0: 5, 1: 5}, "python": {0: 3, 1: 3}}
        assert "2024" not in suggest_queries(index, documents)

    def test_skips_terms_that_appear_in_too_many_documents(self):
        # The same trick IDF pulls: a word in most of the corpus says nothing
        # about it, so it can't be a useful example query.
        documents = {i: {"length": 100} for i in range(10)}
        index = {
            "everywhere": {i: 5 for i in range(10)},
            "selective": {0: 5, 1: 5},
        }
        suggestions = suggest_queries(index, documents)
        assert "everywhere" not in suggestions
        assert "selective" in suggestions

    def test_each_document_contributes_at_most_one_suggestion(self):
        # Ten words off the same page is one suggestion wearing ten hats.
        documents = {i: {"length": 100} for i in range(6)}
        index = {
            "alpha": {0: 9, 1: 1},
            "bravo": {0: 8, 1: 1},
            "charl": {0: 7, 1: 1},
            "delta": {2: 9, 3: 1},
        }
        suggestions = suggest_queries(index, documents, limit=4)
        assert len(suggestions) == len(set(suggestions))
        # alpha/bravo/charlie all describe doc 0 best, so only one may survive.
        assert len({"alpha", "bravo", "charl"} & set(suggestions)) == 1

    def test_respects_the_limit(self, index, documents):
        assert len(suggest_queries(index, documents, limit=1)) <= 1

    def test_empty_corpus_yields_no_suggestions(self):
        assert suggest_queries({}, {}) == []

    def test_is_deterministic(self, index, documents):
        assert suggest_queries(index, documents) == suggest_queries(index, documents)
