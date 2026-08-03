"""
retrieval.py — given query tokens, find candidate documents from the index.

Strategy: OR / union. A document is a candidate if it contains ANY query term.
We gather candidates cheaply here and leave the ordering to the ranking stage.
"""

from indexer import load_index
from query import process_query


def retrieve(query_tokens: list[str], index: dict) -> set[int]:
    """Return the set of doc IDs that contain at least one query term."""
    candidates: set[int] = set()
    for term in query_tokens:
        postings = index.get(term, {})   # {} if the term isn't in the index at all
        candidates.update(postings.keys())   # add every doc ID that has this term
    return candidates


if __name__ == "__main__":
    data = load_index("index.json")
    documents, index = data["documents"], data["index"]

    raw = "python web"
    tokens = process_query(raw)
    candidate_ids = retrieve(tokens, index)

    print(f"Query:      {raw!r}")
    print(f"Tokens:     {tokens}")
    print(f"Candidates: {sorted(candidate_ids)}\n")

    for doc_id in sorted(candidate_ids):
        print(f"  [{doc_id}] {documents[doc_id]['url']}")
