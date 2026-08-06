"""
server.py — the JSON API the React frontend talks to.

This file is deliberately thin. Every interesting decision was already made in
the modules underneath it; the server's only jobs are to load the index ONCE
and to translate HTTP requests into calls we already have.

The load-once part is the whole architecture of a search engine in miniature.
Parsing, indexing, and PageRank are expensive, so they happen at startup —
before any user is waiting. A request then does nothing but tokenize, look up
postings, and score a handful of candidates. That asymmetry (slow offline work,
fast online lookups) is why search can answer in milliseconds.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from indexer import load_index
from pagerank import compute_pagerank
from query import process_query
from ranking import search
from snippets import highlight, make_snippet

# Populated at startup. Everything here is read-only once built, so requests can
# share it freely without locking.
STATE: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Do the expensive work once, at boot, instead of per request."""
    data = load_index("index.json")
    STATE["documents"] = data["documents"]
    STATE["index"] = data["index"]

    # Authority depends only on the link graph, never on the query — so it's
    # computed here, not inside the request handler.
    STATE["authority"] = compute_pagerank(STATE["documents"])

    print(
        f"Ready: {len(STATE['documents'])} documents, "
        f"{len(STATE['index'])} unique terms"
    )
    yield
    STATE.clear()


app = FastAPI(title="Zoogle", lifespan=lifespan)

# The Vite dev server runs on a different port (5173) than this API (8000), so
# the browser treats it as a cross-origin request and blocks it by default.
# This is a DEV convenience — in production the frontend is built to static
# files and served from one origin, and this stops being necessary.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/search")
def search_endpoint(
    q: str = Query("", description="the user's raw query"),
    limit: int = Query(10, ge=1, le=50),
):
    """Run the full online path and return ranked results with snippets."""
    documents, index = STATE["documents"], STATE["index"]

    tokens = process_query(q)
    if not tokens:
        # Empty or punctuation-only query. Not an error — just nothing to do.
        return {"query": q, "tokens": [], "count": 0, "results": []}

    ranked = search(q, index, documents, authority=STATE["authority"], limit=limit)

    results = []
    for doc_id, score in ranked:
        doc = documents[doc_id]
        snippet = make_snippet(doc["text"], tokens)
        results.append({
            "id": doc_id,
            "url": doc["url"],
            "title": doc["title"],
            "score": round(score, 4),
            # Segments, not HTML — the frontend decides what a match looks like.
            "snippet": highlight(snippet, tokens),
        })

    return {"query": q, "tokens": tokens, "count": len(results), "results": results}


@app.get("/api/stats")
def stats_endpoint():
    """Corpus overview — handy for the empty state before anyone searches."""
    documents = STATE["documents"]
    authority = STATE["authority"]
    return {
        "documents": len(documents),
        "terms": len(STATE["index"]),
        "pages": [
            {
                "id": doc_id,
                "url": doc["url"],
                "title": doc["title"],
                "length": doc["length"],
                "authority": round(authority.get(doc_id, 0.0), 4),
            }
            for doc_id, doc in sorted(
                documents.items(), key=lambda p: -authority.get(p[0], 0.0)
            )
        ],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
