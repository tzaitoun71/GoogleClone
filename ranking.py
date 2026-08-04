"""
ranking.py — order candidate documents by relevance using TF-IDF.

Retrieval answers "which documents *could* match?". Ranking answers "which of
them match *best*?" — and it's allowed to be more expensive, because it only
ever looks at the candidates retrieval handed us, not the whole corpus.

TF-IDF is the product of two intuitions:

    TF  (term frequency)          how much this document is ABOUT the term.
                                  'python' appearing 6 times in a 108-token page
                                  is a stronger signal than appearing once.

    IDF (inverse document freq)   how much the term NARROWS THINGS DOWN.
                                  'database' appears in 1 of 6 docs — rare, so
                                  informative. 'the' appears in all 6 — it tells
                                  us nothing, so it should count for nothing.

A document's score is the sum, over the query's terms, of TF x IDF. High score
means: this page uses the query's rare words, a lot.

TF-IDF alone only reads the words inside a page, so we blend in a second,
independent signal — PageRank authority from the link graph. See combine() for
how the two are mixed, and why relevance has to stay in charge.
"""

import math

from indexer import load_index
from pagerank import compute_pagerank
from query import process_query
from retrieval import retrieve

# How much the final score listens to authority vs. relevance. Kept low on
# purpose: PageRank doesn't know what you searched for.
AUTHORITY_WEIGHT = 0.2


def term_frequency(term: str, doc_id: int, index: dict, documents: dict) -> float:
    """How often `term` occurs in this document, as a FRACTION of its length.

    We divide by document length so a long page can't win just by being long.
    Without this, a 10,000-word page mentioning 'python' twice would outrank a
    focused 100-word page mentioning it twice.
    """
    raw_count = index.get(term, {}).get(doc_id, 0)
    if raw_count == 0:
        return 0.0
    return raw_count / documents[doc_id]["length"]


def inverse_document_frequency(term: str, index: dict, num_docs: int) -> float:
    """How rare `term` is across the corpus: log(N / df).

    df ("document frequency") is how many documents contain the term.
      - term in 1 of 6 docs -> log(6/1) = 1.79   very informative
      - term in 5 of 6 docs -> log(6/5) = 0.18   weak signal
      - term in 6 of 6 docs -> log(6/6) = 0.00   worthless, contributes nothing

    That last case is the useful part: stopwords like 'the' and 'and' zero
    themselves out automatically. We never needed a stopword list — the math
    discovers which words are noise.
    """
    df = len(index.get(term, {}))
    if df == 0:  # term isn't in the corpus at all
        return 0.0
    return math.log(num_docs / df)


def score_document(
    query_tokens: list[str], doc_id: int, index: dict, documents: dict
) -> float:
    """Sum TF x IDF over every query term for one document."""
    num_docs = len(documents)
    return sum(
        term_frequency(term, doc_id, index, documents)
        * inverse_document_frequency(term, index, num_docs)
        for term in query_tokens
    )


def normalize(scores: dict[int, float]) -> dict[int, float]:
    """Rescale scores to 0..1 by dividing by the largest.

    Necessary because TF-IDF and PageRank live on totally different scales
    (~0.03 vs ~0.20 in this corpus). Adding them raw would let the bigger
    numbers win by accident rather than by meaning.
    """
    largest = max(scores.values(), default=0.0)
    if largest == 0:
        return {doc_id: 0.0 for doc_id in scores}
    return {doc_id: score / largest for doc_id, score in scores.items()}


def combine(
    relevance: dict[int, float],
    authority: dict[int, float],
    authority_weight: float = AUTHORITY_WEIGHT,
) -> dict[int, float]:
    """Blend the two signals into one score, after putting them on one scale.

    Note that relevance is normalized ACROSS THE CANDIDATES for this query,
    while authority is a fixed property of the corpus. So the question each
    document answers is "how relevant am I compared to the other results?"
    combined with "how well-regarded am I in general?"

    The weight matters more than it looks. Push it too high and index.html —
    the most-linked page — wins every search regardless of what was typed.
    Authority is a tiebreaker between relevant pages, not a substitute for
    being relevant.
    """
    relevance = normalize(relevance)
    authority = normalize({doc_id: authority.get(doc_id, 0.0) for doc_id in relevance})
    return {
        doc_id: (1 - authority_weight) * relevance[doc_id]
        + authority_weight * authority[doc_id]
        for doc_id in relevance
    }


def rank(
    query_tokens: list[str],
    candidates: set[int],
    index: dict,
    documents: dict,
    authority: dict[int, float] | None = None,
    limit: int | None = None,
) -> list[tuple[int, float]]:
    """Score every candidate and return (doc_id, score) pairs, best first.

    With no `authority` passed this is pure TF-IDF; pass PageRank scores to get
    the blended ranking. Ties break on doc ID so ordering is deterministic.
    """
    relevance = {
        doc_id: score_document(query_tokens, doc_id, index, documents)
        for doc_id in candidates
    }
    final = relevance if authority is None else combine(relevance, authority)

    scored = sorted(final.items(), key=lambda pair: (-pair[1], pair[0]))
    return scored[:limit] if limit is not None else scored


def search(
    raw_query: str,
    index: dict,
    documents: dict,
    authority: dict[int, float] | None = None,
    limit: int | None = None,
):
    """The whole online path in one call: query -> tokens -> candidates -> ranked."""
    tokens = process_query(raw_query)
    candidates = retrieve(tokens, index)
    return rank(tokens, candidates, index, documents, authority, limit)


if __name__ == "__main__":
    data = load_index("index.json")
    documents, index = data["documents"], data["index"]
    num_docs = len(documents)

    # Authority is query-independent, so it's computed once, offline in spirit —
    # not per search. Only the relevance half depends on what was typed.
    authority = compute_pagerank(documents)

    for raw in ["python web", "database", "the and"]:
        tokens = process_query(raw)
        relevance_only = search(raw, index, documents)
        blended = search(raw, index, documents, authority=authority)

        print(f"Query:  {raw!r}  ->  {tokens}")
        print("  IDF:  " + ", ".join(
            f"{t}={inverse_document_frequency(t, index, num_docs):.2f}" for t in tokens
        ))

        print(f"  {'TF-IDF only':<34}   {'+ PageRank authority':<34}")
        for position, ((left_id, left), (right_id, right)) in enumerate(
            zip(relevance_only, blended), start=1
        ):
            moved = "  <-- moved" if left_id != right_id else ""
            print(
                f"  {position}. {left:.4f} [{left_id}] {documents[left_id]['url']:<22}"
                f"   {right:.4f} [{right_id}] {documents[right_id]['url']:<22}{moved}"
            )
        print()
