"""retrieval.py — turning query tokens into candidate documents."""

from retrieval import retrieve


class TestRetrieve:
    def test_is_a_union_not_an_intersection(self, index, documents):
        # OR semantics: containing ANY query term makes a document a candidate.
        # Requiring all of them would make two-word queries return nothing.
        python_only = retrieve(["python"], index)
        databases_only = retrieve(["databases"], index)
        both = retrieve(["python", "databases"], index)
        assert both == python_only | databases_only
        assert both > python_only

    def test_unknown_terms_contribute_no_candidates(self, index):
        assert retrieve(["zzzznotpresent"], index) == set()

    def test_unknown_terms_do_not_suppress_known_ones(self, index):
        assert retrieve(["python", "zzzznotpresent"], index) == retrieve(
            ["python"], index
        )

    def test_empty_query_yields_no_candidates(self, index):
        assert retrieve([], index) == set()

    def test_repeated_terms_do_not_duplicate_candidates(self, index):
        assert retrieve(["python", "python"], index) == retrieve(["python"], index)

    def test_returns_document_ids_that_exist(self, index, documents):
        assert retrieve(["python"], index) <= set(documents)
