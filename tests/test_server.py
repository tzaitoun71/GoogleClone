"""server.py — the thin translation layer between HTTP and everything else.

The handlers are called directly rather than over HTTP. The interesting
behaviour is the shape of the response and the empty-query guard, and going
through a real client would only add a dependency to test the same thing.
"""

import pytest

import server
from pagerank import compute_pagerank
from ranking import average_length
from suggest import suggest_queries


@pytest.fixture
def api(built):
    """Populate the module-level STATE the way the lifespan hook does."""
    documents, index = built["documents"], built["index"]
    server.STATE.update(
        documents=documents,
        index=index,
        authority=compute_pagerank(documents),
        avgdl=average_length(documents),
        suggestions=suggest_queries(index, documents, limit=5),
    )
    yield server
    server.STATE.clear()


class TestSearchEndpoint:
    def test_returns_ranked_results_with_snippets(self, api):
        payload = api.search_endpoint(q="python", limit=10, method="bm25")

        assert payload["query"] == "python"
        assert payload["tokens"] == ["python"]
        assert payload["count"] == len(payload["results"])
        assert payload["results"]

        first = payload["results"][0]
        assert set(first) == {"id", "url", "title", "score", "snippet"}
        # Snippets are segments for the frontend to style, never HTML.
        assert all(set(seg) == {"text", "match"} for seg in first["snippet"])

    def test_empty_query_is_not_an_error(self, api):
        payload = api.search_endpoint(q="", limit=10, method="bm25")
        assert payload == {"query": "", "tokens": [], "count": 0, "results": []}

    def test_punctuation_only_query_is_not_an_error(self, api):
        assert api.search_endpoint(q="!!!", limit=10, method="bm25")["count"] == 0

    def test_query_matching_nothing_returns_an_empty_result_set(self, api):
        payload = api.search_endpoint(q="zzzznotpresent", limit=10, method="bm25")
        assert payload["count"] == 0
        assert payload["results"] == []

    def test_limit_is_respected(self, api):
        payload = api.search_endpoint(q="python databases", limit=1, method="bm25")
        assert payload["count"] <= 1

    def test_both_scorers_are_reachable(self, api):
        bm25 = api.search_endpoint(q="python", limit=10, method="bm25")
        tfidf = api.search_endpoint(q="python", limit=10, method="tfidf")
        assert bm25["method"] == "bm25"
        assert tfidf["method"] == "tfidf"

    def test_scores_are_ordered_best_first(self, api):
        results = api.search_endpoint(q="python databases", limit=10, method="bm25")[
            "results"
        ]
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)


class TestStatsEndpoint:
    def test_describes_the_loaded_corpus(self, api, documents, index):
        payload = api.stats_endpoint()
        assert payload["documents"] == len(documents)
        assert payload["terms"] == len(index)
        assert len(payload["pages"]) == len(documents)

    def test_pages_are_ordered_by_authority(self, api):
        authorities = [page["authority"] for page in api.stats_endpoint()["pages"]]
        assert authorities == sorted(authorities, reverse=True)

    def test_suggestions_are_included_for_the_empty_state(self, api):
        assert isinstance(api.stats_endpoint()["suggestions"], list)
