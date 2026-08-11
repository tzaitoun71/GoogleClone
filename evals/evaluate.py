"""evals/evaluate.py — measure whether a ranking change actually helped.

Every other module in this project can tell you WHAT it ranked. None of them
can tell you whether that ranking was any good. Without this, "is BM25 better
than TF-IDF?" and "is 0.2 the right authority weight?" get answered by running
a query, squinting at the output, and forming an impression — which is exactly
how you end up tuning a search engine backwards.

So: a set of queries with known-good answers (judgments.json), and two standard
metrics over them.

    P@1     Did the top result get it right? The strictest measure, and the one
            closest to what a user actually experiences — almost nobody looks
            past the first result.

    MRR     Mean reciprocal rank. Credits being close: the right page at #2 is
            worth 1/2, at #4 it's worth 1/4, past #10 it's worth nothing. This
            is what tells you a change moved things in the right direction even
            when it didn't flip the winner.

Both are averaged over the judged queries. Higher is better; 1.0 means every
query put a correct page first.

Usage
-----
    uv run python evals/evaluate.py                 both scorers, shipped weight
    uv run python evals/evaluate.py --sweep         sweep the authority weight
    uv run python evals/evaluate.py --per-query     show where it goes wrong
    uv run python evals/evaluate.py --index other.json
"""

import argparse
import json
import sys
from pathlib import Path

# The engine modules live at the repo root, one level up, and import each other
# by bare name. Adding the root to sys.path lets this run as a plain script
# ("python evals/evaluate.py") as well as a module ("python -m evals.evaluate").
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from indexer import load_index  # noqa: E402
from pagerank import compute_pagerank  # noqa: E402
from query import process_query  # noqa: E402
from ranking import (  # noqa: E402
    AUTHORITY_WEIGHT,
    average_length,
    combine,
    relevance_scores,
)
from retrieval import retrieve  # noqa: E402

JUDGMENTS_PATH = Path(__file__).parent / "judgments.json"
CUTOFF = 10  # MRR looks this far down; nothing below it earns credit


def rank_with_weight(raw_query, index, documents, authority, avgdl, method, weight):
    """The online path, with the authority weight left as a parameter.

    Deliberately mirrors ranking.rank() step for step. It is not called
    directly because rank() hardcodes AUTHORITY_WEIGHT via combine()'s default,
    and sweeping that constant is most of the point of this script.
    """
    tokens = process_query(raw_query)
    candidates = retrieve(tokens, index)
    relevance = relevance_scores(
        tokens, candidates, index, documents, method, avgdl
    )
    final = combine(relevance, authority, authority_weight=weight)
    return sorted(final.items(), key=lambda pair: (-pair[1], pair[0]))


def evaluate(queries, index, documents, authority, avgdl, method, weight):
    """Return (p_at_1, mrr, per_query) for one configuration."""
    hits = 0
    reciprocal_total = 0.0
    per_query = []

    for raw_query, relevant_ids in queries.items():
        ranked = rank_with_weight(
            raw_query, index, documents, authority, avgdl, method, weight
        )

        # Position of the first correct page, 1-indexed. None if it never shows.
        found_at = None
        for position, (doc_id, _score) in enumerate(ranked[:CUTOFF], start=1):
            if doc_id in relevant_ids:
                found_at = position
                break

        if found_at == 1:
            hits += 1
        if found_at is not None:
            reciprocal_total += 1.0 / found_at

        top_url = documents[ranked[0][0]]["url"] if ranked else None
        per_query.append((raw_query, found_at, top_url))

    total = len(queries)
    return hits / total, reciprocal_total / total, per_query


def load_queries(documents, path):
    """Read judgments and translate target URLs into doc IDs.

    A judged page that isn't in the corpus is a corpus mismatch, not a ranking
    failure — scoring it as a miss would quietly blame the ranker for indexing
    the wrong thing. Those queries are dropped and reported instead.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))["queries"]
    url_to_id = {doc["url"]: doc_id for doc_id, doc in documents.items()}

    queries, skipped = {}, []
    for raw_query, urls in raw.items():
        ids = {url_to_id[url] for url in urls if url in url_to_id}
        if ids:
            queries[raw_query] = ids
        else:
            skipped.append(raw_query)

    return queries, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--index", default="index.json", help="index to evaluate")
    parser.add_argument("--judgments", default=str(JUDGMENTS_PATH))
    parser.add_argument("--sweep", action="store_true", help="sweep authority weight")
    parser.add_argument("--per-query", action="store_true", help="show each query")
    args = parser.parse_args()

    if not Path(args.index).exists():
        sys.exit(
            f"No index at {args.index!r}. Build one first:\n"
            f"  uv run python indexer.py crawled"
        )

    data = load_index(args.index)
    documents, index = data["documents"], data["index"]
    authority = compute_pagerank(documents)
    avgdl = average_length(documents)

    queries, skipped = load_queries(documents, args.judgments)
    if not queries:
        sys.exit(
            f"None of the judged pages are in {args.index!r}. The judgments "
            f"describe the quotes.toscrape.com crawl — build that index with:\n"
            f"  uv run python crawler.py https://quotes.toscrape.com --max-pages 40\n"
            f"  uv run python indexer.py crawled"
        )

    print(f"{len(documents)} documents · {len(queries)} judged queries")
    if skipped:
        print(f"skipped (target not in this corpus): {', '.join(skipped)}")
    print()

    if args.sweep:
        print(f"{'':>6}  {'bm25':^17}   {'tfidf':^17}")
        print(f"{'w':>6}  {'P@1':>7} {'MRR':>8}   {'P@1':>7} {'MRR':>8}")
        best = None
        for step in range(0, 21):
            weight = step / 20
            row = [weight]
            for method in ("bm25", "tfidf"):
                p1, mrr, _ = evaluate(
                    queries, index, documents, authority, avgdl, method, weight
                )
                row += [p1, mrr]
                if best is None or p1 > best[0] or (p1 == best[0] and mrr > best[1]):
                    best = (p1, mrr, method, weight)
            marker = "  <-- shipped default" if weight == AUTHORITY_WEIGHT else ""
            print(
                f"{row[0]:>6.2f}  {row[1]:>7.3f} {row[2]:>8.3f}"
                f"   {row[3]:>7.3f} {row[4]:>8.3f}{marker}"
            )
        print(
            f"\nbest: {best[2]} at authority weight {best[3]:.2f} "
            f"— P@1 {best[0]:.3f}, MRR {best[1]:.3f}"
        )
        return

    print(f"authority weight {AUTHORITY_WEIGHT} (ranking.AUTHORITY_WEIGHT)\n")
    print(f"{'scorer':>7}  {'P@1':>7} {'MRR':>8}")
    results = {}
    for method in ("bm25", "tfidf"):
        p1, mrr, per_query = evaluate(
            queries, index, documents, authority, avgdl, method, AUTHORITY_WEIGHT
        )
        results[method] = per_query
        print(f"{method:>7}  {p1:>7.3f} {mrr:>8.3f}")

    if args.per_query:
        for method, per_query in results.items():
            print(f"\n{method}")
            for raw_query, found_at, top_url in per_query:
                where = f"#{found_at}" if found_at else f"not in top {CUTOFF}"
                mark = "ok  " if found_at == 1 else "MISS"
                short = (top_url or "").replace("https://quotes.toscrape.com", "")
                print(f"  {mark} {raw_query:<18} {where:<14} top1={short}")


if __name__ == "__main__":
    main()
