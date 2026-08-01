# MiniGoogle

A small, from-scratch search engine that implements the core stages of a modern web
search pipeline: crawling, parsing, indexing, retrieval, and ranking. The goal is
educational — to build a working, end-to-end search engine simple enough to fully
understand, while mirroring the real architecture Google uses.

The central principle: a search engine does not query the live web on each request. It
crawls and indexes content ahead of time, then answers every query with a fast lookup
against that precomputed index.

## Features

- **Crawler** — downloads pages, extracts links, and traverses the link graph while
  respecting `robots.txt`.
- **Parser** — converts raw HTML into structured documents (title, text, tokens, links).
- **Inverted index** — maps terms to the documents that contain them, with term
  frequency and position data for ranking.
- **Query processing** — normalizes and tokenizes queries to match the index.
- **Retrieval** — resolves query terms against the index to produce candidate documents.
- **Ranking** — orders results using TF-IDF relevance and PageRank authority.
- **Web UI** — a search interface that returns ranked results with snippets.

## Architecture

The pipeline has two halves. Crawling, parsing, and indexing run **offline** and are
repeated periodically. Query processing, retrieval, and ranking run **online**, on every
request, and are optimized for latency.

```text
                      PAGES (web or local corpus)
                               │
                               ▼
                        ┌─────────────┐
                        │   Crawler   │   download pages, follow links
                        └──────┬──────┘
                               │  raw HTML
                               ▼
                        ┌─────────────┐
                        │   Parser    │   extract title, text, links
                        └──────┬──────┘
                               │  clean documents
                               ▼
                        ┌─────────────┐
                        │   Indexer   │   build the inverted index
                        └──────┬──────┘
                               │  index on disk
                               ▼
   ┌───────────────────────────────────────────────────────┐
   │                    QUERY TIME                           │
   │                                                         │
   │  User query ──►  Query Processor  (normalize, tokenize) │
   │                       │                                 │
   │                       ▼                                 │
   │                  Retrieval        (lookup in index)     │
   │                       │                                 │
   │                       ▼                                 │
   │                  Ranking          (TF-IDF + PageRank)   │
   │                       │                                 │
   │                       ▼                                 │
   │               Results + Snippets                        │
   └───────────────────────────────┬───────────────────────┘
                                    │
                                    ▼
                                  User
```

### Pipeline stages

| Stage | Responsibility |
| --- | --- |
| Crawling | Download pages, follow links, respect `robots.txt`, avoid duplicates. |
| Parsing | Strip HTML to structured data; normalize text (lowercase, tokenize). |
| Indexing | Build an inverted index (term → documents) with frequency and position. |
| Query processing | Apply the same normalization to the query as to documents. |
| Retrieval | Look up query terms in the index to gather candidate documents. |
| Ranking | Score candidates with TF-IDF and PageRank; return the best first. |
| Serving | Run retrieval and ranking, generate snippets, return the results page. |

### The inverted index

The inverted index is the core data structure. Rather than scanning every document per
query, terms are mapped to the documents that contain them:

```text
Documents:
  1: "cats are friendly"
  2: "dogs are friendly"
  3: "cats and dogs"

Inverted index:
  cats     → {1, 3}
  dogs     → {2, 3}
  friendly → {1, 2}
```

A search for `cats` immediately resolves to `{1, 3}` without scanning the corpus.

## Project structure

```text
minigoogle/
├── corpus/              # local sample pages used as the initial dataset
├── crawler.py           # fetch pages, follow links, save raw HTML
├── parser.py            # HTML -> {title, text, tokens, links}
├── indexer.py           # build and persist the inverted index
├── index_store.py       # load/save the index
├── query.py             # normalize and tokenize a query
├── retrieval.py         # index lookup -> candidate documents
├── ranking.py           # TF-IDF + PageRank -> ordered results
├── pagerank.py          # PageRank over the link graph
├── server.py            # web UI
└── tests/               # unit tests per stage
```

## Tech stack

- **Language:** Python 3
- **Crawling / parsing:** `requests`, `beautifulsoup4`
- **Web UI:** a lightweight web framework (Flask or FastAPI)

The implementation favors the standard library and adds dependencies only where they
provide clear value.

## Roadmap

Development follows the data flow, so each stage has real input to build against.

- [x] README and architecture
- [ ] Local corpus — interlinked HTML pages as a reproducible, offline dataset
- [ ] Parser — convert an HTML file into a structured document
- [ ] Indexer — build the inverted index over the corpus
- [ ] Query and retrieval — resolve query terms to matching documents
- [ ] Ranking — order results with TF-IDF
- [ ] PageRank — add an authority signal from the link graph
- [ ] Web UI — search box and results page
- [ ] Crawler — fetch live URLs while respecting `robots.txt`

## Core concept

The essence of the engine in a few lines:

```python
documents = {
    1: "Python is a programming language",
    2: "Java is used for backend development",
    3: "Python is useful for data science",
}

index = {}
for doc_id, text in documents.items():
    for word in text.lower().split():
        index.setdefault(word, set()).add(doc_id)

query = "python"
for doc_id in index.get(query, set()):
    print(documents[doc_id])
```

Crawling, TF-IDF, PageRank, snippets, and the web UI are all infrastructure built
around this single idea: precompute the mapping from terms to documents, then answer
queries by reading that mapping.
