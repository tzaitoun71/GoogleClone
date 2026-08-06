"""
ranking.py — order candidate documents by relevance, with BM25 or TF-IDF.

Retrieval answers "which documents *could* match?". Ranking answers "which of
them match *best*?" — and it's allowed to be more expensive, because it only
ever looks at the candidates retrieval handed us, not the whole corpus.

Both scorers here are built from the same two intuitions:

    TF  (term frequency)          how much this document is ABOUT the term.
                                  'python' appearing 6 times in a 108-token page
                                  is a stronger signal than appearing once.

    IDF (inverse document freq)   how much the term NARROWS THINGS DOWN.
                                  'database' appears in 1 of 40 docs — rare, so
                                  informative. 'the' appears in all of them — it
                                  tells us nothing, so it should count for
                                  nothing.

They differ in HOW they combine those, and the difference matters as soon as
documents vary in length:

    tfidf   TF is count / length, multiplied by IDF. Simple and readable, but
            it divides by the document's FULL length, which punishes long pages
            hard. On a real crawl a 53-token tag page mentioning "einstein"
            once beat the 650-token Einstein page mentioning him twelve times.

    bm25    The standard fix, and the default. Two changes: term counts
            SATURATE (the 20th mention adds almost nothing over the 19th), and
            length normalization is PARTIAL and measured against the corpus
            average rather than absolute. See score_bm25().

Relevance only reads the words inside a page, so we blend in a second,
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

# BM25's two knobs. These defaults are the ones the literature settled on after
# a lot of evaluation, and they're a sane place to stay.
#
#   K1  how fast term counts saturate. Lower = the 2nd occurrence already adds
#       little. At k1 = 0 only presence matters, not count at all.
#   B   how much document length is normalized away. b = 1 reproduces TF-IDF's
#       full division by length; b = 0 ignores length entirely. 0.75 sits
#       deliberately near the "mostly normalize, but not completely" middle.
K1 = 1.5
B = 0.75


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


def average_length(documents: dict) -> float:
    """Mean document length, BM25's yardstick for "is this page long?".

    Length only means anything relative to the rest of the corpus: 650 tokens
    is long among tag stubs and short among journal articles. Computed once and
    passed in — recomputing it per query would walk the entire doc store on
    every request, which is exactly the offline work we moved to startup.
    """
    if not documents:
        return 0.0
    return sum(doc["length"] for doc in documents.values()) / len(documents)


def bm25_idf(term: str, index: dict, num_docs: int) -> float:
    """BM25's IDF: log(1 + (N - df + 0.5) / (df + 0.5)).

    Same idea as the plain log(N/df) above — rare terms count for more — but
    smoothed with the +0.5 terms so it stays positive and well-behaved even for
    a term that appears in every single document. The classic formula hits
    exactly 0 there; this one approaches 0 without the cliff.
    """
    df = len(index.get(term, {}))
    if df == 0:
        return 0.0
    return math.log(1 + (num_docs - df + 0.5) / (df + 0.5))


def score_bm25(
    query_tokens: list[str],
    doc_id: int,
    index: dict,
    documents: dict,
    avgdl: float,
    k1: float = K1,
    b: float = B,
) -> float:
    """BM25 relevance for one document.

        score = Σ  idf(t) · ────────── f · (k1 + 1) ──────────
                             f + k1 · (1 - b + b · len/avgdl)

    Read the fraction as TF with two corrections applied:

    SATURATION. As f grows, the ratio approaches (k1 + 1) and stops. Going from
    1 to 2 mentions is a big jump; 19 to 20 is nearly nothing. This is the fix
    for a page that repeats a word without being any more about it — and, back
    when people wrote pages to game search engines, the fix for keyword
    stuffing.

    PARTIAL LENGTH NORMALIZATION. The denominator scales by len/avgdl, but only
    by the fraction b of it. At b = 1 you'd be back to dividing fully by length
    (TF-IDF's behavior, which over-punished the 650-token Einstein page). At
    b = 0.75, long documents are discounted — just not so hard that a 53-token
    stub with one mention can beat them.
    """
    length = documents[doc_id]["length"]
    num_docs = len(documents)
    score = 0.0

    for term in query_tokens:
        count = index.get(term, {}).get(doc_id, 0)
        if count == 0:
            continue
        # Guard avgdl == 0 (empty corpus) so the ratio stays defined.
        normalized = 1 - b + b * (length / avgdl if avgdl else 1)
        score += bm25_idf(term, index, num_docs) * (
            count * (k1 + 1) / (count + k1 * normalized)
        )

    return score


def relevance_scores(
    query_tokens: list[str],
    candidates: set[int],
    index: dict,
    documents: dict,
    method: str = "bm25",
    avgdl: float | None = None,
) -> dict[int, float]:
    """Score candidates with the chosen scorer. Both are kept so you can compare."""
    if method == "tfidf":
        return {
            doc_id: score_document(query_tokens, doc_id, index, documents)
            for doc_id in candidates
        }

    if avgdl is None:
        avgdl = average_length(documents)
    return {
        doc_id: score_bm25(query_tokens, doc_id, index, documents, avgdl)
        for doc_id in candidates
    }


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
    method: str = "bm25",
    avgdl: float | None = None,
) -> list[tuple[int, float]]:
    """Score every candidate and return (doc_id, score) pairs, best first.

    With no `authority` passed this is pure relevance; pass PageRank scores to
    get the blended ranking. Ties break on doc ID so ordering is deterministic.
    """
    relevance = relevance_scores(
        query_tokens, candidates, index, documents, method, avgdl
    )
    final = relevance if authority is None else combine(relevance, authority)

    scored = sorted(final.items(), key=lambda pair: (-pair[1], pair[0]))
    return scored[:limit] if limit is not None else scored


def search(
    raw_query: str,
    index: dict,
    documents: dict,
    authority: dict[int, float] | None = None,
    limit: int | None = None,
    method: str = "bm25",
    avgdl: float | None = None,
):
    """The whole online path in one call: query -> tokens -> candidates -> ranked."""
    tokens = process_query(raw_query)
    candidates = retrieve(tokens, index)
    return rank(tokens, candidates, index, documents, authority, limit, method, avgdl)


if __name__ == "__main__":
    import sys

    data = load_index("index.json")
    documents, index = data["documents"], data["index"]
    avgdl = average_length(documents)

    # Authority is query-independent, so it's computed once, offline in spirit —
    # not per search. Only the relevance half depends on what was typed.
    authority = compute_pagerank(documents)

    queries = sys.argv[1:] or ["python web", "database", "the and"]
    print(f"{len(documents)} documents, average length {avgdl:.0f} tokens\n")

    for raw in queries:
        tokens = process_query(raw)
        old = search(raw, index, documents, authority, 5, method="tfidf")
        new = search(raw, index, documents, authority, 5, method="bm25", avgdl=avgdl)

        print(f"Query: {raw!r}  ->  {tokens}")
        print(f"  {'TF-IDF':<52} {'BM25':<52}")

        for position, ((old_id, old_score), (new_id, new_score)) in enumerate(
            zip(old, new), start=1
        ):
            moved = "  <--" if old_id != new_id else ""
            print(
                f"  {position}. {old_score:.3f} {documents[old_id]['url'][:44]:<46}"
                f" {new_score:.3f} {documents[new_id]['url'][:44]:<46}{moved}"
            )
        print()
