"""
suggest.py — propose example queries by reading the index itself.

The landing page needs a few queries worth trying, and hardcoding them only
works until the corpus changes. After a crawl, "python web" suggests searches
against a corpus that no longer exists. So we derive them instead.

What makes a good suggestion is narrower than it first appears:

    it must return something      a term nothing contains is a dead end, and a
                                  term only ONE page contains teaches nothing
                                  about ranking.

    it must be discriminating     'the' is in every document, so searching it
                                  says nothing about the corpus. We cap
                                  document frequency at half the corpus, which
                                  drops stopwords without a stopword list —
                                  the same trick IDF pulls.

    it must be characteristic     the best suggestions are words some page is
                                  really ABOUT, which is exactly what a high
                                  BM25 score means. So we rank each term by its
                                  best score in any single document.

    they must differ from each    ten words off the same page is one suggestion
    other                         wearing ten hats, so each document may
                                  contribute only one.
"""

from ranking import average_length, score_bm25

MIN_TERM_LENGTH = 4    # shorter tokens are mostly function words
MIN_DOCUMENT_FREQ = 2  # a term in one page can't demonstrate ranking


def suggest_queries(index: dict, documents: dict, limit: int = 4) -> list[str]:
    """Return example queries drawn from the strongest terms in the index."""
    num_docs = len(documents)
    if num_docs == 0:
        return []

    avgdl = average_length(documents)

    # In a small corpus "half the documents" is only two or three pages, so
    # floor the cap at MIN_DOCUMENT_FREQ or the band would be empty.
    max_document_freq = max(MIN_DOCUMENT_FREQ, num_docs // 2)

    scored: list[tuple[float, str, int]] = []
    for term, postings in index.items():
        if len(term) < MIN_TERM_LENGTH or term.isdigit():
            continue
        if not MIN_DOCUMENT_FREQ <= len(postings) <= max_document_freq:
            continue

        # The document this term describes best, and how well it describes it.
        best_doc = max(
            postings, key=lambda doc_id: score_bm25([term], doc_id, index, documents, avgdl)
        )
        best_score = score_bm25([term], best_doc, index, documents, avgdl)
        scored.append((best_score, term, best_doc))

    scored.sort(key=lambda row: (-row[0], row[1]))  # term breaks score ties

    suggestions: list[str] = []
    claimed: set[int] = set()
    for _, term, best_doc in scored:
        if best_doc in claimed:
            continue          # this page already contributed a suggestion
        claimed.add(best_doc)
        suggestions.append(term)
        if len(suggestions) == limit:
            break

    return suggestions


if __name__ == "__main__":
    from indexer import load_index

    data = load_index("index.json")
    documents, index = data["documents"], data["index"]

    print(f"{len(documents)} documents, {len(index)} terms")
    print("suggestions:", suggest_queries(index, documents, limit=6))
