"""
pagerank.py — score documents by AUTHORITY, using only the link graph.

TF-IDF reads the words inside a document. PageRank never looks at the text at
all — it only asks who links to whom. The two signals are independent on
purpose: one measures "is this page about your query?", the other measures "do
other pages treat this one as worth pointing at?"

The idea, in one sentence: a page is important if important pages link to it.

That's circular, and the circularity is the point. We resolve it by iterating —
start by assuming every page is equally important, then repeatedly let each page
hand its current score to the pages it links to. Do that enough times and the
numbers stop moving: the scores that survive are the fixed point of the graph.

Two details make it actually work:

    Damping (d = 0.85)   Model a "random surfer" who follows a link 85% of the
                         time and jumps to a random page the other 15%. Without
                         those random jumps, score gets permanently trapped in
                         cycles of pages that only link to each other.

    Dangling nodes       A page with no outbound links has score to give and
                         nowhere to put it. That score would silently leak out
                         of the system every iteration, so we redistribute it
                         evenly across all pages instead.
"""

from indexer import load_index

DAMPING = 0.85


def build_link_graph(documents: dict) -> dict[int, list[int]]:
    """Turn the stored URL links into a doc_id -> [doc_id, ...] graph.

    The parser saved links as URLs ("python.html"); PageRank needs integer IDs.
    Along the way we drop three kinds of edge:
      - links to pages outside our corpus (nothing to score)
      - self-links (a page can't vouch for itself)
      - duplicate links (linking twice shouldn't count twice)
    """
    url_to_id = {doc["url"]: doc_id for doc_id, doc in documents.items()}

    graph: dict[int, list[int]] = {}
    for doc_id, doc in documents.items():
        targets: list[int] = []
        for url in doc["links"]:
            target_id = url_to_id.get(url)
            if target_id is None or target_id == doc_id or target_id in targets:
                continue
            targets.append(target_id)
        graph[doc_id] = targets
    return graph


def pagerank(
    graph: dict[int, list[int]],
    damping: float = DAMPING,
    max_iterations: int = 100,
    tolerance: float = 1e-6,
) -> dict[int, float]:
    """Iterate the random-surfer model until the scores stop changing.

    Returns {doc_id: score}, where the scores sum to 1.0 — each one is the
    probability of finding the surfer on that page at any given moment.
    """
    num_docs = len(graph)
    if num_docs == 0:
        return {}

    # Start with no information: every page is equally likely.
    scores = {doc_id: 1.0 / num_docs for doc_id in graph}

    for _ in range(max_iterations):
        # Score from pages that link nowhere. It has to go somewhere, so it gets
        # spread evenly rather than vanishing.
        dangling_score = sum(
            scores[doc_id] for doc_id, targets in graph.items() if not targets
        )

        # Every page starts the round with the random-jump share: the (1 - d)
        # chance the surfer teleports here, plus its cut of the dangling score.
        base = (1.0 - damping) / num_docs + damping * dangling_score / num_docs
        next_scores = {doc_id: base for doc_id in graph}

        # Then each page splits its own score evenly among the pages it links to.
        # A page linking to 2 others gives each a bigger share than one linking
        # to 20 — an endorsement means less when it's handed out freely.
        for doc_id, targets in graph.items():
            if not targets:
                continue
            share = damping * scores[doc_id] / len(targets)
            for target_id in targets:
                next_scores[target_id] += share

        # Converged once no single page moved meaningfully this round.
        delta = sum(abs(next_scores[doc_id] - scores[doc_id]) for doc_id in graph)
        scores = next_scores
        if delta < tolerance:
            break

    return scores


def compute_pagerank(documents: dict, damping: float = DAMPING) -> dict[int, float]:
    """Convenience wrapper: documents -> link graph -> PageRank scores."""
    return pagerank(build_link_graph(documents), damping=damping)


if __name__ == "__main__":
    data = load_index("index.json")
    documents = data["documents"]
    graph = build_link_graph(documents)

    print("Link graph (who points at whom):")
    for doc_id, targets in graph.items():
        arrows = ", ".join(f"[{t}]" for t in targets) or "(none)"
        print(f"  [{doc_id}] {documents[doc_id]['url']:<22} -> {arrows}")

    # Inbound counts, for contrast. PageRank is NOT just this — it weighs each
    # inbound link by the authority of the page it came from.
    inbound: dict[int, int] = {doc_id: 0 for doc_id in graph}
    for targets in graph.values():
        for target_id in targets:
            inbound[target_id] += 1

    scores = compute_pagerank(documents)

    print(f"\nPageRank (damping={DAMPING}, scores sum to {sum(scores.values()):.4f}):")
    for doc_id, score in sorted(scores.items(), key=lambda p: (-p[1], p[0])):
        print(
            f"  {score:.4f}  [{doc_id}] {documents[doc_id]['url']:<22}"
            f" {inbound[doc_id]} inbound"
        )
