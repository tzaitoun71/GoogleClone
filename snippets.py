"""
snippets.py — build the little excerpt shown under each search result.

A ranked list of URLs isn't a search engine yet. The snippet is what lets you
decide whether to click, and it has one job: show the query's words IN CONTEXT.

So the problem isn't "summarize this document" — it's "find the densest patch
of query terms in this document, and quote it." A snippet for 'python' should
land on the sentence that talks about python, not on the first 30 words of the
page.

The approach:
  1. Slide a fixed-width window over the document's tokens.
  2. Score each window by how many of the query's terms it contains, preferring
     windows that cover MORE DISTINCT terms over ones that repeat a single term.
  3. Quote the original text under the best window — not the tokens, so the
     user sees real punctuation and capitalization.

Step 3 is why we kept `text` in the doc store alongside `tokens`. Tokens are
for matching; the original text is for reading.
"""

from parser import TOKEN_RE

SNIPPET_WIDTH = 30   # tokens shown around the match
ELLIPSIS = "…"


def make_snippet(
    text: str, query_tokens: list[str], width: int = SNIPPET_WIDTH
) -> str:
    """Return the passage of `text` that best covers the query terms."""
    # finditer (not findall) because we need each token's CHARACTER POSITION in
    # the original string — that's what lets us slice real text back out.
    matches = list(TOKEN_RE.finditer(text.lower()))
    if not matches:
        return ""

    tokens = [m.group() for m in matches]
    wanted = set(query_tokens)

    # Where does any query term appear? These are the only places worth looking.
    hits = [i for i, token in enumerate(tokens) if token in wanted]

    if not hits:
        # No query term in the text at all (it was a candidate via another
        # term). Fall back to the opening of the document, like a summary.
        start = 0
    else:
        # Only consider windows CENTERED on a hit. Sliding over every possible
        # start would work too, but this checks a handful of windows instead of
        # hundreds, and any window worth picking contains a hit anyway.
        best_start, best_score = 0, -1
        for hit in hits:
            candidate = max(0, hit - width // 2)
            window = tokens[candidate : candidate + width]

            # Distinct terms dominate raw count: a window covering 'python' AND
            # 'web' beats one with 'python' five times. Matching more of what
            # was asked for is better than matching one part emphatically.
            distinct = len(wanted.intersection(window))
            total = sum(1 for token in window if token in wanted)
            score = distinct * 100 + total

            if score > best_score:      # strict > keeps the EARLIEST best window
                best_start, best_score = candidate, score
        start = best_start

    end = min(start + width, len(tokens))

    # Slice the ORIGINAL text between the first and last token of the window.
    passage = text[matches[start].start() : matches[end - 1].end()]

    # Collapse the newlines and indentation inherited from the HTML source into
    # single spaces. Done AFTER slicing, so the character offsets above still
    # line up with the text we measured them against.
    passage = " ".join(passage.split())

    prefix = ELLIPSIS + " " if start > 0 else ""
    suffix = " " + ELLIPSIS if end < len(tokens) else ""
    return f"{prefix}{passage}{suffix}"


def highlight(snippet: str, query_tokens: list[str]) -> list[dict]:
    """Split a snippet into [{text, match}] segments for the UI to style.

    We deliberately return DATA, not HTML with <b> tags in it. The backend
    saying "these characters matched" and the frontend deciding what that looks
    like keeps us from injecting markup into React — and document text is
    untrusted input the moment a real crawler starts fetching pages.
    """
    wanted = set(query_tokens)
    segments: list[dict] = []
    cursor = 0

    def add(text: str, match: bool) -> None:
        if not text:
            return
        # Merge with the previous segment if it's the same kind, so the UI gets
        # "the quick" rather than "the", " ", "quick".
        if segments and segments[-1]["match"] == match:
            segments[-1]["text"] += text
        else:
            segments.append({"text": text, "match": match})

    for m in TOKEN_RE.finditer(snippet.lower()):
        add(snippet[cursor : m.start()], False)          # punctuation / spaces
        add(snippet[m.start() : m.end()], m.group() in wanted)
        cursor = m.end()

    add(snippet[cursor:], False)                          # trailing text
    return segments


if __name__ == "__main__":
    from indexer import load_index
    from query import process_query

    documents = load_index("index.json")["documents"]

    for raw in ["python web", "database", "nothing matches here"]:
        tokens = process_query(raw)
        print(f"Query: {raw!r}")
        for doc_id, doc in documents.items():
            snippet = make_snippet(doc["text"], tokens)
            print(f"  [{doc_id}] {doc['url']:<22} {snippet[:100]}")
        print()

    print("Highlight segments for 'python web' on doc 4:")
    tokens = process_query("python web")
    snippet = make_snippet(documents[4]["text"], tokens)
    for segment in highlight(snippet, tokens):
        marker = "**" if segment["match"] else "  "
        print(f"  {marker}{segment['text']!r}")
