"""
indexer.py - build the inverted index over the whole corpus.

Runs the parser on every page, assigns each integer doc ID, and produces two
structures:

    index[term]   -> { doc_id: term_frequency}  # find documents by word
    documents[id] -> { url, title, text, ... }  # describe a document by id

Together these are everything retrieval and ranking need. We save them to a
single JSON file so the (slow) build runs offline, and query time just loads it.
"""

import json
from collections import Counter
from pathlib import Path

from parser import parse_file

def build_index(corpus_dir: str = "corpus") -> dict:
    """Parse every .html file in corpus_dir and build the index + doc store."""
    documents: dict[int, dict] = {}
    index: dict[str, dict[int, int]] = {}

    # sorted() so IDs are assigned deterministically - the same page always gets
    # the same ID across runs, which makes debugging and tests sane.
    files = sorted(Path(corpus_dir).glob("*.html"))

    for doc_id, path in enumerate(files):
        doc = parse_file(path)

        # Store everything we'll need later to describe this document.
        documents[doc_id] = {
            "url": doc["url"],
            "title": doc["title"],
            "text": doc["text"],
            "length": len(doc["tokens"]), # total tokens, used to normalize TF later
            "links": doc["links"],
        }

        # Count how often each term appears in this document...
        term_frequencies = Counter(doc["tokens"])

        # ...and fold those coutns into the inverted index.
        for term, freq in term_frequencies.items():
            index.setdefault(term, {})[doc_id] = freq

    return {"documents": documents, "index": index}

def save_index(data: dict, path: str = "index.json") -> None:
      """Persist the index to disk as JSON."""
      Path(path).write_text(json.dumps(data), encoding="utf-8")


def load_index(path: str = "index.json") -> dict:
    """Load the index. Note: JSON turns int keys into strings, so we convert the
    doc-store keys and posting keys back to ints on the way in."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    documents = {int(k): v for k, v in data["documents"].items()}
    index = {
        term: {int(doc_id): freq for doc_id, freq in postings.items()}
        for term, postings in data["index"].items()
    }
    return {"documents": documents, "index": index}


if __name__ == "__main__":
    data = build_index("corpus")
    save_index(data)

    documents, index = data["documents"], data["index"]
    print(f"Indexed {len(documents)} documents")
    print(f"Vocabulary: {len(index)} unique terms\n")

    for doc_id, doc in documents.items():
        print(f"  [{doc_id}] {doc['url']:<22} {doc['length']} tokens")

    print("\nPostings for a few terms:")
    for term in ["python", "database", "javascript", "web"]:
        print(f"  {term:<12} -> {index.get(term, {})}")