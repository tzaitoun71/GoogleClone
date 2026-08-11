"""pagerank.py — authority from the link graph.

PageRank's correctness lives in a handful of invariants rather than in any
particular score, so that is what these check: the scores form a probability
distribution, nothing leaks, and the graph is cleaned up before it is used.
"""

import pytest

from pagerank import DAMPING, build_link_graph, compute_pagerank, pagerank


class TestBuildLinkGraph:
    def test_maps_urls_to_integer_ids(self, documents):
        graph = build_link_graph(documents)
        assert set(graph) == set(documents)
        assert all(isinstance(t, int) for targets in graph.values() for t in targets)

    def test_drops_self_links(self, documents):
        # delta.html links to itself; a page can't vouch for itself.
        graph = build_link_graph(documents)
        for doc_id, targets in graph.items():
            assert doc_id not in targets

    def test_drops_duplicate_links(self, documents):
        # delta.html links to gamma twice.
        graph = build_link_graph(documents)
        for targets in graph.values():
            assert len(targets) == len(set(targets))

    def test_drops_links_to_pages_outside_the_corpus(self, documents):
        documents[0]["links"] = documents[0]["links"] + ["http://elsewhere.com/x"]
        graph = build_link_graph(documents)
        assert all(t in documents for targets in graph.values() for t in targets)

    def test_records_a_dangling_page_as_an_empty_list(self, documents):
        gamma = next(i for i, d in documents.items() if d["url"] == "gamma.html")
        assert build_link_graph(documents)[gamma] == []


class TestPageRank:
    def test_scores_sum_to_one(self, documents):
        scores = compute_pagerank(documents)
        assert sum(scores.values()) == pytest.approx(1.0)

    def test_dangling_score_is_redistributed_not_leaked(self):
        # 0 -> 1, and 1 links nowhere. Without the dangling correction, score
        # drains out of the system a little more on every iteration.
        scores = pagerank({0: [1], 1: []})
        assert sum(scores.values()) == pytest.approx(1.0)

    def test_a_linked_page_outranks_an_unlinked_one(self, documents):
        scores = compute_pagerank(documents)
        gamma = next(i for i, d in documents.items() if d["url"] == "gamma.html")
        alpha = next(i for i, d in documents.items() if d["url"] == "alpha.html")
        assert scores[gamma] > scores[alpha]

    def test_more_inbound_links_means_more_authority(self):
        # 3 pages all point at 3; 0 gets nothing.
        scores = pagerank({0: [3], 1: [3], 2: [3], 3: []})
        assert scores[3] > scores[0]

    def test_a_link_from_a_focused_page_is_worth_more(self):
        # 0 links only to 2. 1 spreads itself across 3 and 4 as well. Both
        # start equal, so 2 should end up ahead of 3.
        scores = pagerank({0: [2], 1: [3, 4, 5], 2: [], 3: [], 4: [], 5: []})
        assert scores[2] > scores[3]

    def test_graph_with_no_links_at_all_is_uniform(self):
        scores = pagerank({0: [], 1: [], 2: []})
        assert scores[0] == pytest.approx(scores[1]) == pytest.approx(scores[2])
        assert sum(scores.values()) == pytest.approx(1.0)

    def test_empty_graph_is_handled(self):
        assert pagerank({}) == {}
        assert compute_pagerank({}) == {}

    def test_is_deterministic(self, documents):
        assert compute_pagerank(documents) == compute_pagerank(documents)

    def test_damping_of_one_still_conserves_total_score(self):
        # No random jumps at all — the case where dangling handling is the only
        # thing keeping the total from decaying.
        scores = pagerank({0: [1], 1: []}, damping=1.0)
        assert sum(scores.values()) == pytest.approx(1.0)

    def test_zero_damping_gives_every_page_the_same_score(self):
        # A surfer who never follows links can't prefer anything.
        scores = pagerank({0: [1], 1: [2], 2: []}, damping=0.0)
        assert len(set(round(s, 12) for s in scores.values())) == 1

    def test_converges_well_before_the_iteration_cap(self, documents):
        graph = build_link_graph(documents)
        assert pagerank(graph, max_iterations=100) == pytest.approx(
            pagerank(graph, max_iterations=1000)
        )

    def test_default_damping_is_the_documented_value(self):
        assert DAMPING == 0.85
