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
"""

import math

from indexer import load_index
from query import process_query
from retrieval import retrieve


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


def rank(
    query_tokens: list[str],
    candidates: set[int],
    index: dict,
    documents: dict,
    limit: int | None = None,
) -> list[tuple[int, float]]:
    """Score every candidate and return (doc_id, score) pairs, best first.

    Ties break on doc ID so the ordering is deterministic across runs.
    """
    scored = [
        (doc_id, score_document(query_tokens, doc_id, index, documents))
        for doc_id in candidates
    ]
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    return scored[:limit] if limit is not None else scored


def search(raw_query: str, index: dict, documents: dict, limit: int | None = None):
    """The whole online path in one call: query -> tokens -> candidates -> ranked."""
    tokens = process_query(raw_query)
    candidates = retrieve(tokens, index)
    return rank(tokens, candidates, index, documents, limit)


if __name__ == "__main__":
    data = load_index("index.json")
    documents, index = data["documents"], data["index"]
    num_docs = len(documents)

    for raw in ["python web", "database", "the and"]:
        tokens = process_query(raw)
        results = search(raw, index, documents)

        print(f"Query:  {raw!r}  ->  {tokens}")
        print("  IDF:  " + ", ".join(
            f"{t}={inverse_document_frequency(t, index, num_docs):.2f}" for t in tokens
        ))
        for rank_position, (doc_id, score) in enumerate(results, start=1):
            print(f"  {rank_position}. {score:.4f}  [{doc_id}] {documents[doc_id]['url']}")
        print()
