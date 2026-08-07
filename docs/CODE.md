# Zoogle — Code Walkthrough

Every module, every function, and the reasoning behind each decision.

This document explains **how the code works**. Its companion,
[CONCEPTS.md](CONCEPTS.md), explains **why search engines work this way** —
what TF-IDF and BM25 actually mean, why we switched between them, and how this
relates to what Google does. If a formula here feels unmotivated, that's the
document that motivates it.

---

## Table of contents

1. [The shape of the system](#1-the-shape-of-the-system)
2. [The two data structures everything revolves around](#2-the-two-data-structures-everything-revolves-around)
3. [`parser.py` — HTML in, structured document out](#3-parserpy)
4. [`query.py` — the shortest file in the project](#4-querypy)
5. [`indexer.py` — building and persisting the index](#5-indexerpy)
6. [`retrieval.py` — finding candidates](#6-retrievalpy)
7. [`ranking.py` — ordering candidates](#7-rankingpy)
8. [`pagerank.py` — authority from the link graph](#8-pagerankpy)
9. [`snippets.py` — the excerpt under each result](#9-snippetspy)
10. [`suggest.py` — example queries from the index](#10-suggestpy)
11. [`crawler.py` — fetching real pages](#11-crawlerpy)
12. [`server.py` — the JSON API](#12-serverpy)
13. [The frontend](#13-the-frontend)
14. [Invariants — rules the code must never break](#14-invariants)
15. [Running everything](#15-running-everything)

---

## 1. The shape of the system

The pipeline splits into two halves that run at completely different times.

```
OFFLINE (slow, occasional)          ONLINE (fast, every request)
─────────────────────────           ────────────────────────────
crawler.py   fetch pages            query.py      normalize the query
    ↓                                   ↓
parser.py    HTML → tokens          retrieval.py  index lookup → candidates
    ↓                                   ↓
indexer.py   build inverted index   ranking.py    score and order them
    ↓                                   ↓
index.json   saved to disk          snippets.py   build the excerpts
                                        ↓
                                    server.py     JSON → React
```

**This split is the single most important idea in the codebase.** Crawling,
parsing, indexing, and PageRank are expensive — seconds to minutes. They happen
before any user is waiting. A search request then does almost nothing: tokenize
a few words, look up a few dictionary keys, score a handful of candidates.

That's why search feels instant. It isn't that the work is fast; it's that the
work already happened.

| File | Stage | Runs |
|---|---|---|
| `crawler.py` | fetch pages over HTTP | offline |
| `parser.py` | HTML → title, text, tokens, links | offline |
| `indexer.py` | build + save/load the inverted index | offline |
| `pagerank.py` | authority scores from the link graph | at startup |
| `suggest.py` | example queries derived from the index | at startup |
| `query.py` | normalize the user's raw query | online |
| `retrieval.py` | which documents *could* match | online |
| `ranking.py` | which documents match *best* | online |
| `snippets.py` | the excerpt shown under each result | online |
| `server.py` | HTTP API tying the online path together | online |

---

## 2. The two data structures everything revolves around

Almost every function in this project takes one or both of these. Understanding
them means understanding most of the code.

### The inverted index

```python
index = {
    "python":   {0: 1, 2: 1, 4: 6, 5: 1},
    "database": {1: 3},
}
```

Read it as: **term → {document id → how many times it appears there}**.

So `"python"` appears in documents 0, 2, 4, and 5 — six times in document 4,
once in each of the others. The inner dictionary is called a **postings list**.

It's "inverted" because the natural direction is document → words. Flipping it
to word → documents is what lets a search skip the corpus entirely. A search
for `python` is one dictionary lookup, not a scan of every page.

### The document store

```python
documents = {
    4: {
        "url":    "python.html",
        "title":  "Python Programming Language",
        "text":   "Python Python is a general purpose programming language...",
        "length": 108,
        "links":  ["data-science.html", "web-development.html", ...],
    },
}
```

Read it as: **document id → everything we know about that document**.

Each field earns its place:

- `url` — displayed in results, and the key PageRank matches links against.
- `title` — displayed in results.
- `text` — the original readable text, used *only* to build snippets. Tokens are
  for matching; text is for reading.
- `length` — total token count. Both scorers divide by this to stop long pages
  winning on bulk alone.
- `links` — outbound links, the raw material for PageRank.

Documents are keyed by **integer IDs**, not URLs. Integers are compact in the
postings lists (which is most of the index by size), and they're stable — as
long as we assign them deterministically, which is why `indexer.py` sorts
filenames before enumerating.

---

## 3. `parser.py`

**Job:** turn one messy HTML page into a clean, structured document.

### The token pattern

```python
TOKEN_RE = re.compile(r"[a-z0-9]+")
```

A token is a run of lowercase letters and digits. Everything else —
punctuation, whitespace, symbols — is a separator and gets dropped.

Note it only matches *lowercase*. That's not a bug: `tokenize()` lowercases the
text before matching, so the pattern never needs an uppercase range. Doing it
in that order means there is exactly one place where case is handled.

### `tokenize()`

```python
def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())
```

Two operations: lowercase, then extract every run of letters/digits.

```
"Best PYTHON web-development!"  →  ['best', 'python', 'web', 'development']
```

Notice `web-development` became two tokens. That's a real tradeoff — a search
for `web` now matches it, but a search for the exact phrase `web-development`
has no way to require them adjacent. Supporting that needs a **positional
index** (storing *where* in the document each term occurs, not just how often),
which is how real engines do phrase search.

**This function is the most important two lines in the project.** It is used on
documents at index time *and* on queries at search time. If those two paths ever
normalized differently, nothing would ever match. See [Invariants](#14-invariants).

### `normalize_url()`

The URL equivalent of `tokenize()` — reduce a URL to one canonical spelling so
the same page always has the same name.

```python
url, _ = urldefrag(url)
parts = urlsplit(url)
```

`urldefrag` strips `#section`. A fragment scrolls the browser to a position on a
page; it never identifies a different document. `urlsplit` breaks the rest into
scheme / netloc / path / query / fragment.

```python
scheme, netloc = parts.scheme.lower(), parts.netloc.lower()
if (scheme == "http" and netloc.endswith(":80")) or (
    scheme == "https" and netloc.endswith(":443")
):
    netloc = netloc.rsplit(":", 1)[0]
```

Scheme and host are case-insensitive per the URL spec, so `HTTP://Example.COM`
and `http://example.com` are the same host. **Paths are not lowercased** —
`/Page.html` and `/page.html` can genuinely be different files on a
case-sensitive server.

Then the default ports are dropped: `http://x.com:80/a` is by definition the
same as `http://x.com/a`.

```python
kept = [
    (key, value)
    for key, value in parse_qsl(parts.query, keep_blank_values=True)
    if not key.lower().startswith(TRACKING_PARAMS)
]
```

Campaign tags (`utm_source`, `fbclid`, `gclid`…) identify *where a visitor came
from*, not *what page they landed on*. Two URLs differing only in these are the
same document, and indexing both would duplicate it.

`keep_blank_values=True` preserves `?debug=` — a parameter with an empty value
is still a parameter, and dropping it could change which page you get.

```python
path = parts.path or ("/" if netloc else "")
return urlunsplit((scheme, netloc, path, urlencode(kept), ""))
```

`http://example.com` and `http://example.com/` are the same page, so an empty
path becomes `/`. But the `if netloc` guard matters enormously: a bare filename
like `"python.html"` has no host, and forcing it to `/` would rewrite it to
nonsense. That guard is what keeps the original hand-written corpus working
after we added crawler support.

The final empty string in `urlunsplit` is the fragment — dropped for good.

### `parse_html()`

```python
soup = BeautifulSoup(html, "html.parser")
```

`"html.parser"` is Python's built-in parser — no external C dependency, and
tolerant of the broken markup real pages are full of.

```python
title = ""
if soup.title and soup.title.string:
    title = soup.title.string.strip()
elif soup.h1:
    title = soup.h1.get_text(strip=True)
```

Prefer `<title>`, fall back to the first `<h1>`, else empty string. The
`soup.title.string` check guards a real case: `<title></title>` exists but has
no string, and `.strip()` on `None` would crash.

```python
for tag in soup(["script", "style"]):
    tag.decompose()
```

**This must happen before extracting text.** Script and style tags contain code,
not prose. Without this, JavaScript variable names and CSS class names end up in
your index, and a search for `function` matches every page on the site.

`decompose()` destroys the tag and its contents in place.

```python
text = soup.get_text(separator=" ", strip=True)
```

`separator=" "` is doing real work. Without it, `<p>Hello</p><p>World</p>` would
yield `HelloWorld` — one token instead of two.

```python
links = []
for a in soup.find_all("a", href=True):
    target = normalize_url(urljoin(url, a["href"]))
    if target and target not in links:
        links.append(target)
```

`find_all("a", href=True)` skips anchors without an `href` (used as jump
targets), which would otherwise raise a `KeyError`.

`urljoin(url, href)` resolves relative links against the page's own URL:
`urljoin("https://x.com/docs/a", "b.html")` → `https://x.com/docs/b.html`. This
is why the parser needs to know its own URL.

Then `normalize_url` canonicalizes, and `not in links` de-duplicates — a page
linking to the same target twice is one endorsement, not two. A list rather
than a set because order is human-readable when debugging, and these lists are
short.

### `parse_file()`

```python
def parse_file(path: str | Path, url: str | None = None) -> dict:
    path = Path(path)
    html = path.read_text(encoding="utf-8")
    return parse_html(html, url=url or path.name)
```

The `url or path.name` fallback is what lets one function serve two worlds. The
hand-written corpus has no URLs, so `python.html` becomes both filename and
identity. Crawled pages pass their real `https://…` URL. Nothing downstream
cares which it was.

---

## 4. `query.py`

The entire module:

```python
from parser import tokenize

def process_query(raw_query: str) -> list[str]:
    return tokenize(raw_query)
```

It looks pointless, and it is the opposite of pointless. It exists to make the
**symmetry between documents and queries structural rather than accidental.**

Because `process_query` is defined as "whatever `tokenize` does", any future
change — stemming, synonyms, accent folding — automatically applies to both
sides. If instead every call site did its own `.lower().split()`, the two paths
would drift the first time anyone touched one of them, and matching would break
in ways that are very hard to see.

It's also the natural home for things queries need but documents don't: spelling
correction, `site:` filters, quoted phrases.

---

## 5. `indexer.py`

**Job:** run the parser over a whole directory and build the two data
structures, then save them.

### `build_index()`

```python
manifest_path = Path(corpus_dir) / "manifest.json"
manifest = (
    json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest_path.exists()
    else {}
)
```

The crawler saves pages under slugified filenames
(`quotes.toscrape.com-tag-love-a3f9e1.html`) and records the real URL for each
in `manifest.json`. Hand-written corpora have no manifest, so `{}` means "use
filenames as URLs".

**Without this, the link graph collapses.** A crawled page's links point at
`https://quotes.toscrape.com/…`, but if its own recorded URL were the slug
filename, nothing would match, every page would look like it links nowhere, and
PageRank would return a flat uniform score. This actually happened during
development — see the note in `crawler.py`'s manifest-merging logic.

```python
files = sorted(Path(corpus_dir).glob("*.html"))
for doc_id, path in enumerate(files):
```

`sorted()` is not cosmetic. `glob` returns filesystem order, which varies. Sorting
means **document 4 is always python.html across every run** — which makes the
index reproducible, debugging sane, and future tests possible.

```python
    doc = parse_file(path, url=manifest.get(path.name))
    documents[doc_id] = {
        "url": doc["url"],
        "title": doc["title"],
        "text": doc["text"],
        "length": len(doc["tokens"]),
        "links": doc["links"],
    }
```

Note `length` is computed here and stored, rather than recomputed at query time.
It's needed by every scoring call, so it's precomputed once — the offline/online
split again, in miniature.

```python
    term_frequencies = Counter(doc["tokens"])
    for term, freq in term_frequencies.items():
        index.setdefault(term, {})[doc_id] = freq
```

`Counter` turns `['a','b','a']` into `{'a': 2, 'b': 1}` in one pass.

`setdefault(term, {})` returns the existing postings dict for a term, creating an
empty one if it's the first time we've seen that word. Then `[doc_id] = freq`
adds this document's entry. Two lines to invert the whole corpus.

### `save_index()` and `load_index()`

```python
def save_index(data: dict, path: str = "index.json") -> None:
    Path(path).write_text(json.dumps(data), encoding="utf-8")
```

No `indent` — this file is read by machines, and indentation would multiply its
size for nothing.

```python
def load_index(path: str = "index.json") -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    documents = {int(k): v for k, v in data["documents"].items()}
    index = {
        term: {int(doc_id): freq for doc_id, freq in postings.items()}
        for term, postings in data["index"].items()
    }
    return {"documents": documents, "index": index}
```

**JSON has no integer keys.** `json.dumps({0: "a"})` produces `{"0": "a"}`, and
loading it back gives you the *string* `"0"`. Every doc ID would silently become
a string, and `documents[0]` would raise `KeyError` while `documents["0"]`
worked.

These two comprehensions convert them back — at both levels, since doc IDs
appear as document-store keys *and* as postings keys. This is the kind of bug
that produces baffling errors far from its cause, which is why it's handled once,
at the boundary.

---

## 6. `retrieval.py`

**Job:** given query tokens, find which documents are worth scoring.

```python
def retrieve(query_tokens: list[str], index: dict) -> set[int]:
    candidates: set[int] = set()
    for term in query_tokens:
        postings = index.get(term, {})
        candidates.update(postings.keys())
    return candidates
```

That's the whole module.

- `index.get(term, {})` — a term nobody wrote returns an empty dict rather than
  raising. Unknown words contribute nothing instead of breaking the search.
- `candidates.update(postings.keys())` — union. A document is a candidate if it
  contains **any** query term (Boolean OR).
- A `set` because the same document appears under several terms and we want it
  once.

**Why OR and not AND?** AND (requiring every term) gives sharper results but
falls off a cliff: one unusual word and you get nothing. OR never returns
nothing when anything is relevant, and hands the ordering problem to ranking —
where a document matching both terms naturally scores higher anyway. Google
behaves roughly this way: it prefers all terms, but will drop some rather than
show an empty page.

Retrieval deliberately does **no scoring**. Its job is to cheaply shrink
"every document in the corpus" down to "documents worth thinking about". On a
web-scale index this is the difference between examining billions of documents
and examining thousands.

---

## 7. `ranking.py`

**Job:** order the candidates. This is the heart of the engine.

The module holds **two scorers**: the original TF-IDF, and BM25, which replaced
it as the default. Both are kept so you can compare them directly — see
[CONCEPTS.md](CONCEPTS.md) for the full story of why we switched.

### The constants

```python
AUTHORITY_WEIGHT = 0.2
K1 = 1.5
B  = 0.75
```

- `AUTHORITY_WEIGHT` — how much the final score listens to PageRank vs.
  relevance. At 0 authority is ignored; at 1 relevance is.
- `K1` — how fast repeated terms stop helping. Lower saturates sooner.
- `B` — how much document length is normalized away. `b=1` fully divides by
  length (TF-IDF's behavior); `b=0` ignores length entirely.

### TF-IDF: `term_frequency()`

```python
raw_count = index.get(term, {}).get(doc_id, 0)
if raw_count == 0:
    return 0.0
return raw_count / documents[doc_id]["length"]
```

Occurrences as a *fraction* of the document. The division stops a long page
winning purely by having more room to mention things.

The `raw_count == 0` early return isn't only an optimization — it avoids
dividing by a length we don't need to look up.

### TF-IDF: `inverse_document_frequency()`

```python
df = len(index.get(term, {}))
if df == 0:
    return 0.0
return math.log(num_docs / df)
```

`df` — "document frequency" — is how many documents contain the term, which is
exactly the length of its postings list. No extra bookkeeping needed.

On the 6-page corpus:

| term | df | `log(6/df)` | meaning |
|---|---|---|---|
| `database` | 1 | 1.792 | very informative |
| `python` | 4 | 0.405 | moderately informative |
| `web` | 5 | 0.182 | weak |
| `the` | 6 | **0.000** | worthless |

That last row is the elegant part: **a word in every document scores exactly
zero and contributes nothing.** We never wrote a stopword list — the math
discovered which words are noise.

### TF-IDF: `score_document()`

```python
return sum(
    term_frequency(term, doc_id, index, documents)
    * inverse_document_frequency(term, index, num_docs)
    for term in query_tokens
)
```

Sum of TF × IDF across query terms. High score = "this page uses the query's
*rare* words, *often*".

### `average_length()`

```python
if not documents:
    return 0.0
return sum(doc["length"] for doc in documents.values()) / len(documents)
```

BM25's yardstick. Length only means something relative to the corpus — 650
tokens is long among tag stubs and short among journal articles.

It's computed once and passed in. `server.py` calls it at startup, because
walking the entire document store on every request is exactly the offline work
we moved out of the request path.

### `bm25_idf()`

```python
return math.log(1 + (num_docs - df + 0.5) / (df + 0.5))
```

The same idea as classic IDF, smoothed. The `+0.5` terms keep it defined and
positive in every case.

The textbook probabilistic form is `log((N - df + 0.5) / (df + 0.5))` **without**
the `1 +`, and it goes *negative* once a term appears in more than half the
corpus — meaning common words would actively subtract from a document's score.
The `1 +` prevents that. This is the variant Lucene and Elasticsearch use.

The cost: a term in every document now scores `0.074` instead of exactly `0`.
The stopword-zeroing property becomes *almost* true rather than exactly true.

### `score_bm25()` — the important one

```
              f · (k1 + 1)
score = Σ idf(t) · ────────────────────────────────
                    f + k1 · (1 - b + b · len/avgdl)
```

```python
length = documents[doc_id]["length"]
num_docs = len(documents)
score = 0.0

for term in query_tokens:
    count = index.get(term, {}).get(doc_id, 0)
    if count == 0:
        continue
    normalized = 1 - b + b * (length / avgdl if avgdl else 1)
    score += bm25_idf(term, index, num_docs) * (
        count * (k1 + 1) / (count + k1 * normalized)
    )
```

Walking the pieces:

**`count == 0: continue`** — terms absent from this document add nothing. Skipping
early avoids pointless arithmetic on what is usually most of the query.

**`normalized = 1 - b + b * (length / avgdl)`** — the length correction, and the
clearest way to see what `b` does:

- `b = 1` → `normalized = length / avgdl` (full normalization, TF-IDF-like)
- `b = 0` → `normalized = 1` (length ignored entirely)
- `b = 0.75` → three-quarters of the way toward full normalization

For an average-length document `length / avgdl = 1`, so `normalized = 1`
regardless of `b`. The correction only bites as documents deviate from average.

The `if avgdl else 1` guard prevents division by zero on an empty corpus.

**`count * (k1 + 1) / (count + k1 * normalized)`** — saturating term frequency.
As `count` grows this approaches `k1 + 1 = 2.5` and stops. Concretely, for an
average-length document:

| occurrences | this factor |
|---|---|
| 1 | 1.00 |
| 2 | 1.43 |
| 5 | 1.92 |
| 12 | 2.22 |
| 100 | 2.46 |
| ∞ | 2.50 |

Going from 1 → 2 mentions is worth a lot. From 12 → 100, almost nothing. That
plateau is BM25's central insight, and the reason it beat TF-IDF on our real
crawl.

### `relevance_scores()`

```python
if method == "tfidf":
    return {doc_id: score_document(...) for doc_id in candidates}

if avgdl is None:
    avgdl = average_length(documents)
return {doc_id: score_bm25(...) for doc_id in candidates}
```

A switch between the two scorers. Keeping both is deliberate: it makes the
comparison runnable rather than theoretical (`ranking.py` prints them side by
side, and the API accepts `?method=tfidf`).

The `avgdl is None` fallback means the function is correct when called casually
from a script, while the server can still pass its precomputed value.

### `normalize()`

```python
largest = max(scores.values(), default=0.0)
if largest == 0:
    return {doc_id: 0.0 for doc_id in scores}
return {doc_id: score / largest for doc_id, score in scores.items()}
```

Rescales scores so the best is 1.0.

Necessary because relevance and PageRank live on different scales — BM25 scores
run around 0.5–2.0, PageRank around 0.02–0.22. Adding them raw would let the
numerically larger signal dominate by accident rather than by design.

`default=0.0` handles an empty candidate set; the `largest == 0` branch handles
the case where every score is zero (a pure-stopword query under TF-IDF) without
dividing by zero.

**A consequence worth knowing:** the top result always scores exactly 1.0 before
blending, no matter how good it actually is. Scores are comparable *within* one
result list, never *across* queries. Fine for ordering — but never show that
number to a user as a "match percentage".

### `combine()`

```python
relevance = normalize(relevance)
authority = normalize({doc_id: authority.get(doc_id, 0.0) for doc_id in relevance})
return {
    doc_id: (1 - authority_weight) * relevance[doc_id]
    + authority_weight * authority[doc_id]
    for doc_id in relevance
}
```

A weighted average of the two normalized signals.

The inner dict comprehension restricts authority to *this query's candidates*
before normalizing. That matters: authority is normalized against the best
candidate, not against the best page in the whole corpus.

Note the asymmetry — relevance is normalized *per query*, authority is a fixed
corpus property. Each document effectively answers: "how relevant am I compared
to the other results?" combined with "how well-regarded am I in general?"

**The weight is the most consequential number in the file.** Set it too high and
the most-linked page wins every search regardless of what was typed. On our
40-page crawl, at `0.2` the homepage takes #1 for `einstein` over the actual
Einstein page; at `0.1` the Einstein page holds. Authority should be a
tiebreaker between relevant pages, not a substitute for being relevant.

### `rank()` and `search()`

```python
relevance = relevance_scores(query_tokens, candidates, index, documents, method, avgdl)
final = relevance if authority is None else combine(relevance, authority)
scored = sorted(final.items(), key=lambda pair: (-pair[1], pair[0]))
return scored[:limit] if limit is not None else scored
```

`key=lambda pair: (-pair[1], pair[0])` sorts by descending score, then by
ascending doc ID. **The tiebreak is what makes results deterministic** — without
it, equally-scored documents would come back in arbitrary dict order and shuffle
between runs.

Passing `authority=None` gives pure relevance, which is how the comparison in
`__main__` isolates BM25's effect from the PageRank blend.

```python
def search(raw_query, index, documents, authority=None, limit=None, method="bm25", avgdl=None):
    tokens = process_query(raw_query)
    candidates = retrieve(tokens, index)
    return rank(tokens, candidates, index, documents, authority, limit, method, avgdl)
```

The entire online path in three lines: normalize → retrieve → rank.

---

## 8. `pagerank.py`

**Job:** score documents by authority, using only the link graph. It never looks
at the text.

### `build_link_graph()`

```python
url_to_id = {doc["url"]: doc_id for doc_id, doc in documents.items()}
```

A reverse lookup, because links are stored as URLs but the algorithm needs
integer IDs.

```python
for doc_id, doc in documents.items():
    targets: list[int] = []
    for url in doc["links"]:
        target_id = url_to_id.get(url)
        if target_id is None or target_id == doc_id or target_id in targets:
            continue
        targets.append(target_id)
    graph[doc_id] = targets
```

Three edges are dropped, each for its own reason:

- **`target_id is None`** — the link points outside our corpus. Nothing to score.
- **`target_id == doc_id`** — a self-link. A page cannot vouch for itself.
- **`target_id in targets`** — a duplicate. Linking twice is one endorsement.

**This function is where URL normalization pays off.** `url_to_id` is keyed by
each document's stored URL; `doc["links"]` holds normalized link targets. They
match only because `normalize_url` was applied to both. If they disagreed by a
trailing slash, every lookup would return `None`, the graph would be empty, and
every page would score identically.

### `pagerank()`

```python
scores = {doc_id: 1.0 / num_docs for doc_id in graph}
```

Start with no information: every page equally likely. With 6 pages, everyone
starts at 0.1667.

```python
for _ in range(max_iterations):
    dangling_score = sum(
        scores[doc_id] for doc_id, targets in graph.items() if not targets
    )
```

A **dangling node** links nowhere. It has score to give and nowhere to put it.
Left alone, that score simply vanishes each round, and eventually *all* score
drains away. We collect it to redistribute.

```python
    base = (1.0 - damping) / num_docs + damping * dangling_score / num_docs
    next_scores = {doc_id: base for doc_id in graph}
```

Every page starts each round with a baseline: its share of the 15% teleport
probability, plus its share of the dangling score. Building `next_scores` fresh
rather than mutating `scores` is essential — every page's contribution must be
computed from the *same* previous state.

```python
    for doc_id, targets in graph.items():
        if not targets:
            continue
        share = damping * scores[doc_id] / len(targets)
        for target_id in targets:
            next_scores[target_id] += share
```

Each page splits 85% of its current score evenly among its targets.

**`/ len(targets)` is the anti-spam heart of PageRank.** A page linking to 2
others gives each half its authority; one linking to 100 gives each a hundredth.
You cannot manufacture authority by linking to everything — you only dilute what
you have.

```python
    delta = sum(abs(next_scores[doc_id] - scores[doc_id]) for doc_id in graph)
    scores = next_scores
    if delta < tolerance:
        break
```

Total movement across all pages. Once it's below `1e-6` the scores have settled
and we stop. The `max_iterations` cap is a safety net; on our corpora
convergence takes well under 100 rounds.

The result: scores summing to 1.0. Each is the probability of finding the random
surfer on that page at any given moment.

---

## 9. `snippets.py`

**Job:** find the most relevant ~30 words of a document and quote them.

The problem is not "summarize this page". It's "find the densest patch of query
terms and show it".

### `make_snippet()`

```python
matches = list(TOKEN_RE.finditer(text.lower()))
if not matches:
    return ""
tokens = [m.group() for m in matches]
```

`finditer`, not `findall` — we need each token's **character position** in the
original string. That's what lets us slice real, punctuated text back out at the
end. `matches[i]` and `tokens[i]` stay index-aligned throughout.

```python
hits = [i for i, token in enumerate(tokens) if token in wanted]
if not hits:
    start = 0
```

No query term in this document at all — possible, since retrieval is OR-based and
the document may have qualified via a different term. Fall back to the opening,
like a summary.

```python
else:
    best_start, best_score = 0, -1
    for hit in hits:
        candidate = max(0, hit - width // 2)
        window = tokens[candidate : candidate + width]
```

Only windows **centered on a hit** are considered. Sliding over every possible
start would also work, but this checks a handful of windows instead of hundreds,
and any window worth choosing contains a hit by definition. `max(0, …)` keeps
the window from starting before the document.

```python
        distinct = len(wanted.intersection(window))
        total = sum(1 for token in window if token in wanted)
        score = distinct * 100 + total
```

**Distinct terms dominate raw count**, by a factor of 100. A window containing
`python` *and* `web` beats one containing `python` five times. Matching more of
what was asked for beats matching one part emphatically. `total` only breaks
ties among windows with equal coverage.

```python
        if score > best_score:
            best_start, best_score = candidate, score
```

Strict `>` means the **earliest** best window wins. With `>=`, a later window
with an identical score would replace it, and snippets would drift toward the
end of documents for no reason.

```python
passage = text[matches[start].start() : matches[end - 1].end()]
passage = " ".join(passage.split())
```

Slice the original text from the first token's start to the last token's end —
punctuation and capitalization intact.

Then collapse whitespace. **Order matters:** the collapse happens *after*
slicing, because it changes string lengths, and doing it first would invalidate
every character offset we just used. HTML source is full of newlines and
indentation that would otherwise appear in the excerpt.

```python
prefix = ELLIPSIS + " " if start > 0 else ""
suffix = " " + ELLIPSIS if end < len(tokens) else ""
```

Ellipses only where text was actually cut.

### `highlight()`

Splits a snippet into `[{text, match}]` segments for the UI.

```python
def add(text: str, match: bool) -> None:
    if not text:
        return
    if segments and segments[-1]["match"] == match:
        segments[-1]["text"] += text
    else:
        segments.append({"text": text, "match": match})
```

Merges adjacent segments of the same kind, so the UI receives
`[{"the quick", false}]` rather than three separate non-match fragments.

```python
for m in TOKEN_RE.finditer(snippet.lower()):
    add(snippet[cursor : m.start()], False)
    add(snippet[m.start() : m.end()], m.group() in wanted)
    cursor = m.end()
add(snippet[cursor:], False)
```

Walk the tokens, emitting the gap before each (punctuation and spaces, never a
match) and then the token itself (a match if it's a query term). The final `add`
catches trailing text after the last token.

**Why segments instead of HTML with `<b>` tags?** Two reasons. React would need
`dangerouslySetInnerHTML` to render server-generated markup — throwing away its
built-in escaping. And document text is *untrusted input* the moment a crawler
starts fetching real pages. The backend reports which characters matched; the
frontend decides what that looks like.

---

## 10. `suggest.py`

**Job:** propose example queries by reading the index, so the landing page
describes whatever corpus is actually loaded.

```python
MIN_TERM_LENGTH = 4
MIN_DOCUMENT_FREQ = 2
```

```python
max_document_freq = max(MIN_DOCUMENT_FREQ, num_docs // 2)
```

A good suggestion sits in a band. Too rare (1 document) and it demonstrates
nothing about ranking; too common (over half the corpus) and it's a stopword.
The `max(…)` floor stops the band collapsing to nothing on a tiny corpus.

```python
for term, postings in index.items():
    if len(term) < MIN_TERM_LENGTH or term.isdigit():
        continue
    if not MIN_DOCUMENT_FREQ <= len(postings) <= max_document_freq:
        continue

    best_doc = max(postings, key=lambda doc_id: score_bm25([term], doc_id, index, documents, avgdl))
    best_score = score_bm25([term], best_doc, index, documents, avgdl)
    scored.append((best_score, term, best_doc))
```

Each surviving term is scored by **its best BM25 score in any single document** —
which is precisely a measure of "some page is really *about* this word".

```python
scored.sort(key=lambda row: (-row[0], row[1]))

suggestions, claimed = [], set()
for _, term, best_doc in scored:
    if best_doc in claimed:
        continue
    claimed.add(best_doc)
    suggestions.append(term)
```

Best first, alphabetical on ties. Then **at most one suggestion per document**,
or you get five words off the same page wearing five hats.

**A known limitation.** On a 6-document corpus this still surfaces `from` and
`used` alongside `records` and `languages` — because `from` and `records` both
appear in exactly 2 of 6 documents with similar counts. There is no statistical
signal separating them at that size. A stopword list would fix it, at the cost
of contradicting how the rest of the engine works. On the 40-page crawl, where
there is real signal, it produces `comedy`, `abilities`, `choices`, `eleanor`,
`mother`.

---

## 11. `crawler.py`

**Job:** fetch real pages over HTTP. The only module that touches the network,
which changes its character completely — every step here can fail, be slow, lie
about its content type, or belong to someone who'd rather we didn't. Most of the
file is restraint, not downloading.

### Constants

```python
USER_AGENT = "ZoogleBot/0.1 (educational search engine project)"
CRAWL_DELAY = 1.0
REQUEST_TIMEOUT = 10
MAX_PAGE_BYTES = 2_000_000
```

An honest User-Agent means an administrator reading their logs can tell what
visited and block it if they want. Without `REQUEST_TIMEOUT`, one server that
accepts a connection and never responds hangs the crawl forever.

### `class Robots`

```python
def _parser_for(self, url: str) -> RobotFileParser | None:
    parts = urlsplit(url)
    host = f"{parts.scheme}://{parts.netloc}"

    if host not in self._parsers:
        parser = RobotFileParser()
        parser.set_url(f"{host}/robots.txt")
        try:
            parser.read()
        except Exception:
            parser = None
        self._parsers[host] = parser
    return self._parsers[host]
```

`robots.txt` is per-host and fetched **once**, then cached. Without caching we'd
request it before every single page, doubling our traffic to a site whose rules
haven't changed since the first request.

The bare `except Exception` is deliberate and correct here: *any* failure to
read robots.txt (missing file, timeout, malformed content) means "no rules
published", and the convention is that absent rules mean no restrictions. A
missing file is not a refusal.

```python
def allowed(self, url: str) -> bool:
    parser = self._parser_for(url)
    return True if parser is None else parser.can_fetch(self.user_agent, url)
```

```python
def delay_for(self, url: str) -> float:
    declared = parser.crawl_delay(self.user_agent) if parser else None
    return max(float(declared or 0), CRAWL_DELAY)
```

`max` means a site asking for a *longer* delay gets it, but a site asking for a
shorter one (or none) doesn't get us going faster than our own floor.

### `slugify()`

```python
stem = f"{parts.netloc}{parts.path}".strip("/")
safe = "".join(char if char.isalnum() or char in "-._" else "-" for char in stem)
safe = safe.strip("-")[:80] or "index"

digest = hashlib.sha1(url.encode()).hexdigest()[:6]
if not safe.endswith(".html"):
    safe += ".html"
return f"{safe[:-5]}-{digest}.html"
```

URLs aren't filenames — they contain `/`, `?`, `:` and can exceed filesystem
limits. This produces something readable (so you can eyeball a crawl directory)
and unique (so `/a/index.html` and `/b/index.html` don't overwrite each other).

**The `hashlib` comment marks a real bug that was caught here.** The first
version used Python's built-in `hash()`. Python randomizes string hashing per
process for security, so the same URL would produce a *different* filename on
every run — and re-crawling would duplicate the entire corpus instead of
updating it. `hashlib.sha1` is deterministic across processes and runs.

### `content_digest()`

```python
return hashlib.sha1(" ".join(text.split()).encode()).hexdigest()
```

Fingerprints a page by its visible text, whitespace-insensitive.

This catches what `normalize_url` **structurally cannot**. `/tag/love/` and
`/tag/love/page/1/` are genuinely different URLs — no canonicalization turns one
into the other. Only the content reveals they're the same page. URL
normalization is syntactic; this is semantic.

Exact hashing catches exact copies. Real crawlers use SimHash or shingling to
catch *near*-duplicates (same article, different ad slot), which is a much
harder problem.

### `fetch()`

```python
try:
    response = session.get(url, timeout=REQUEST_TIMEOUT)
except requests.RequestException as error:
    print(f"    failed: {type(error).__name__}")
    return None

if response.status_code != 200:
    return None

content_type = response.headers.get("content-type", "")
if "html" not in content_type.lower():
    return None

if len(response.content) > MAX_PAGE_BYTES:
    return None

return response.text
```

Four ways a fetch can be unusable, each checked explicitly. The content-type
check matters more than it looks: **a URL doesn't tell you what's behind it.** A
link ending in `.html` can still return a PDF, an image, or JSON, and feeding
binary to an HTML parser produces garbage tokens.

Returning `None` rather than raising lets the crawl loop treat every failure the
same way — skip and continue.

### `crawl()` — the main loop

```python
if fresh:
    for stale in output.glob("*.html"):
        stale.unlink()
    manifest_path.unlink(missing_ok=True)

manifest = json.loads(manifest_path.read_text(...)) if manifest_path.exists() else {}
```

**Crawls accumulate by default.** The manifest from previous runs is loaded and
merged, not overwritten.

This fixed a real bug. The original version wrote a fresh manifest each run, so
crawling a second site erased the URL records of pages already on disk. Those
files stayed, the indexer fell back to slug filenames, every link stopped
resolving, and PageRank silently returned a flat uniform score across a corpus
that looked fine otherwise.

```python
frontier = deque(normalize_url(seed) for seed in seeds)
allowed_hosts = {urlsplit(url).netloc for url in frontier}
seen: set[str] = set(frontier)
```

`deque` because BFS pops from the left and appends to the right, and both are
O(1) on a deque (`list.pop(0)` is O(n)).

**`seen` tracks everything QUEUED, not everything fetched.** URLs go in the
moment they're enqueued. Marking on fetch instead would let the same page enter
the frontier dozens of times before the first copy came back.

```python
content_seen: dict[str, str] = {}
for filename, saved_url in manifest.items():
    path = output / filename
    if path.exists():
        saved = parse_html(path.read_text(encoding="utf-8"), saved_url)
        content_seen[content_digest(saved["text"])] = saved_url
```

Rebuilds fingerprints of everything already on disk, so a re-crawl doesn't add a
page under a second URL just because the first copy came from an earlier run.

```python
fetched = 0
while frontier and fetched < max_pages:
```

**`fetched` counts this run only.** Using `len(manifest)` would make `max_pages` a
cap on the whole corpus — so a second crawl into a directory of 25 pages would
fetch nothing and look like a silent failure.

```python
    delay = robots.delay_for(url)
    elapsed = time.monotonic() - last_fetch.get(host, 0.0)
    if elapsed < delay:
        time.sleep(delay - elapsed)
    last_fetch[host] = time.monotonic()
```

Politeness, measured **per host**. Waiting is only owed to the server we just
talked to, so crawling two hosts doesn't make us twice as slow.

`time.monotonic()` rather than `time.time()` — monotonic clocks never jump
backwards when the system clock is adjusted, which would otherwise produce a
negative elapsed time and skip the delay.

```python
    doc = parse_html(html, url)
    digest = content_digest(doc["text"])

    if digest in content_seen:
        print(f"    duplicate of {content_seen[digest]}")
    else:
        content_seen[digest] = url
        filename = slugify(url)
        (output / filename).write_text(html, encoding="utf-8")
        manifest[filename] = url
        fetched += 1
```

Duplicates are detected and dropped, and **don't consume the page budget** —
`fetched` only increments in the `else` branch.

```python
    for link in doc["links"]:
        if link in seen:
            continue
        if not link.startswith(("http://", "https://")):
            continue
        if same_host_only and urlsplit(link).netloc not in allowed_hosts:
            continue
        seen.add(link)
        frontier.append(link)
```

Links are followed **even from duplicate pages** — the copy we kept may have come
from an earlier run whose frontier is long gone.

The scheme check filters `mailto:`, `javascript:`, and `tel:` links, which have
nothing to fetch.

`same_host_only` is the difference between crawling a *site* and crawling the
*web*. With it on, the frontier stays inside the seeded hosts. Off, one link to
a large site and the crawl never returns.

---

## 12. `server.py`

**Job:** expose the online path over HTTP. Deliberately thin — every interesting
decision was already made in the modules underneath.

### `lifespan()`

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    data = load_index("index.json")
    STATE["documents"] = data["documents"]
    STATE["index"] = data["index"]
    STATE["authority"] = compute_pagerank(STATE["documents"])
    STATE["avgdl"] = average_length(STATE["documents"])
    STATE["suggestions"] = suggest_queries(STATE["index"], STATE["documents"], limit=5)
    yield
    STATE.clear()
```

**This function is the offline/online split made concrete.** Everything before
`yield` runs once at startup; everything after runs at shutdown. Four expensive
things are precomputed:

- the index, loaded from disk
- PageRank, which depends only on the link graph — never on the query
- average document length, for BM25
- suggestions, which describe the corpus rather than any request

A search request then does none of this. `STATE` is a plain dict rather than
globals because it's read-only after startup, so requests share it freely
without locking.

### CORS

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
)
```

The Vite dev server runs on :5173 and this API on :8000, so browsers treat calls
between them as cross-origin and block them by default. A **development
convenience only** — in production the frontend builds to static files served
from one origin. (The Vite proxy makes this redundant in the normal dev setup;
it's kept so hitting :8000 directly also works.)

### `/api/search`

```python
tokens = process_query(q)
if not tokens:
    return {"query": q, "tokens": [], "count": 0, "results": []}
```

An empty or punctuation-only query is **not an error** — there's simply nothing
to do. Returning the same shape as a real response means the frontend needs no
special case.

```python
ranked = search(q, index, documents, authority=STATE["authority"], limit=limit,
                method=method, avgdl=STATE["avgdl"])

for doc_id, score in ranked:
    doc = documents[doc_id]
    snippet = make_snippet(doc["text"], tokens)
    results.append({
        "id": doc_id, "url": doc["url"], "title": doc["title"],
        "score": round(score, 4),
        "snippet": highlight(snippet, tokens),
    })
```

**Snippets are built only for the results being returned**, after `limit` has
been applied. Building them for every candidate would be wasted work on pages
nobody will see.

```python
method: str = Query("bm25", pattern="^(bm25|tfidf)$")
```

FastAPI validates the pattern and returns a 422 for anything else, so an
unexpected value can never reach `relevance_scores`.

### `/api/stats`

Corpus overview for the landing page: document count, vocabulary size,
suggestions, and every page sorted by authority. `authority.get(doc_id, 0.0)`
guards documents missing from the graph.

---

## 13. The frontend

### `vite.config.js`

```js
server: {
  proxy: { '/api': 'http://127.0.0.1:8000' },
}
```

Vite relays `/api/*` to FastAPI, so the browser only ever talks to its own
origin. This is why `api.js` can use bare paths like `/api/search` — and why
those same paths stay correct in production, where the built files would be
served from the same origin as the API.

### `src/api.js`

```js
async function get(path) {
  const response = await fetch(path)
  if (!response.ok) throw new Error(`Request failed: ${response.status}`)
  return response.json()
}

export function search(query, limit = 10) {
  return get(`/api/search?q=${encodeURIComponent(query)}&limit=${limit}`)
}
```

`fetch` only rejects on *network* failure — a 404 or 500 resolves normally with
`ok === false`. Without the explicit check, an error page would be parsed as
JSON and fail confusingly somewhere else.

`encodeURIComponent` so queries containing spaces, `&`, or `#` don't corrupt the
URL.

### `src/App.jsx`

**Two pieces of state, and the split is the whole point of submit-on-Enter:**

```jsx
const [input, setInput] = useState('')
const [submitted, setSubmitted] = useState('')
```

`input` is what's in the box right now; `submitted` is what was actually
searched for. Typing changes only the first. The search effect watches only the
second, so nothing hits the API until you press Enter.

```jsx
const latestRequest = useRef(0)
// ...
const id = ++latestRequest.current
search(submitted).then((data) => {
  if (id !== latestRequest.current) return
  setResponse(data)
})
```

Every request gets a number; a response is applied only if it's still the newest.
Without this, a slow early request could land *after* a fast later one and
overwrite fresh results with stale ones. `useRef` rather than `useState` because
changing it must not trigger a re-render.

```jsx
function handleSubmit(event) {
  event.preventDefault()
  setSubmitted(input.trim())
}
```

Without `preventDefault`, the browser does a full page reload on form submit,
discarding all React state.

```jsx
const searching = submitted.length > 0
```

Keyed off what was **searched**, not what's typed — so the layout doesn't jump
while you're still composing.

```jsx
useEffect(() => {
  function onKeyDown(event) {
    const typing = document.activeElement === inputRef.current
    if (event.key === '/' && !typing) {
      event.preventDefault()
      inputRef.current?.focus()
    } else if (event.key === 'Escape' && typing) {
      inputRef.current?.blur()
    }
  }
  window.addEventListener('keydown', onKeyDown)
  return () => window.removeEventListener('keydown', onKeyDown)
}, [])
```

`/` focuses the box from anywhere; Escape releases it. The `typing` check stops
`/` hijacking the key while you're actually typing a slash, and
`preventDefault` stops the character landing in the box after focus. The cleanup
return is essential — without it, every re-render would stack another listener.

```jsx
{segments.map((segment, i) =>
  segment.match ? <mark key={i}>{segment.text}</mark> : <span key={i}>{segment.text}</span>
)}
```

Renders the backend's segments. Index keys are fine here: the list is static once
rendered and never reordered.

```jsx
const strongest = Math.max(...corpus.pages.map((page) => page.authority)) || 1
```

Bars are scaled to the **strongest page**, not to 1.0 — the real spread is
0.10–0.22, and against a full scale every bar would look identical. The `|| 1`
guards the empty-corpus case, where `Math.max()` of nothing is `-Infinity` and
would produce `NaN` widths.

### The CSS

Two files: `index.css` holds design tokens (colors, fonts, the focus ring),
`App.css` holds layout.

```css
--accent: #ff0048;
--accent-wash: color-mix(in srgb, var(--accent) 10%, transparent);
```

Tinted surfaces are *derived* from the brand color rather than hardcoded, so
changing one hex updates token chips, avatars, authority bars, and focus rings
together.

Dark mode lifts the accent to `#ff3d70`, because saturated red at low luminance
vibrates against dark backgrounds and gets hard to read at small sizes.

---

## 14. Invariants

Rules that aren't enforced by types or tests, but that the system depends on.
Breaking one produces failures that are silent and confusing.

**1. Documents and queries must be tokenized identically.**
`query.py` calls `parser.tokenize` for exactly this reason. If they ever
diverge, matching stops working — not with an error, just with empty results.

**2. Crawler and parser must normalize URLs identically.**
Both call `parser.normalize_url`. If they disagreed, the crawler would save a
page under one spelling while PageRank looked for it under another. The link
graph would come back empty and every page would score the same.

**3. Document IDs must be assigned deterministically.**
`indexer.py` sorts filenames before enumerating. Without it, IDs shuffle between
runs and nothing is reproducible.

**4. `manifest.json` must survive across crawls.**
It's the only record of which URL each saved file came from. Losing it doesn't
lose the pages — it silently destroys the link graph, which is worse, because
everything still appears to work.

**5. Filenames must be deterministic.**
`slugify` uses `hashlib`, not `hash()`, so re-crawling a URL overwrites its own
file instead of creating a new one.

**6. Ranking must break ties deterministically.**
`sorted(key=lambda p: (-p[1], p[0]))` — otherwise equally-scored results shuffle
between identical runs.

---

## 15. Running everything

```bash
# Build an index from the hand-written corpus
uv run python indexer.py

# Or crawl a real site and index that instead
uv run python crawler.py https://quotes.toscrape.com --max-pages 40 --fresh
uv run python indexer.py crawled

# Start the API (port 8000)
uv run python server.py

# Start the UI (port 5173) — in a second terminal
npm run dev --prefix frontend
```

Every module also runs standalone, printing a demonstration of its own stage:

```bash
uv run python parser.py      # parse one page, print the structured result
uv run python indexer.py     # build the index, print postings
uv run python retrieval.py   # show candidate documents for a query
uv run python ranking.py einstein love    # TF-IDF vs BM25, side by side
uv run python pagerank.py    # link graph + authority scores
uv run python snippets.py    # snippets and highlight segments
uv run python suggest.py     # example queries from the current index
```

`ranking.py` accepts queries as arguments, which is the fastest way to see the
scorers disagree on a corpus you just built.

---

*Companion document: [CONCEPTS.md](CONCEPTS.md) — what these algorithms mean and
why they work.*
