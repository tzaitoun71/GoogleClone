# How Search Engines Actually Work

The ideas behind Zoogle — what TF-IDF and BM25 mean, why we replaced one with
the other, how PageRank works, and where all of this sits relative to Google.

This document explains **why**. Its companion, [CODE.md](CODE.md), explains
**how** — line by line. Read this one first if formulas feel arbitrary; read
that one when you want to know what a specific function is doing.

Every number in this document is real, taken from running this code on the two
corpora in the repo: the 6 hand-written pages in `corpus/`, and a 40-page crawl
of `quotes.toscrape.com`.

---

## Table of contents

1. [The one idea](#1-the-one-idea)
2. [Offline and online](#2-offline-and-online)
3. [Tokenization: making text comparable](#3-tokenization)
4. [The inverted index](#4-the-inverted-index)
5. [Retrieval: narrowing down](#5-retrieval)
6. [Ranking, part 1: TF-IDF](#6-ranking-part-1-tf-idf)
7. [Where TF-IDF broke](#7-where-tf-idf-broke)
8. [Ranking, part 2: BM25](#8-ranking-part-2-bm25)
9. [PageRank: authority from links](#9-pagerank)
10. [Combining two signals](#10-combining-two-signals)
11. [Snippets](#11-snippets)
12. [Crawling: the part with rules](#12-crawling)
13. [What Google does that we don't](#13-what-google-does-that-we-dont)
14. [Glossary](#14-glossary)

---

## 1. The one idea

A search engine does not search the web when you type a query.

That sounds obvious once said, but it's the entire architecture. If Google
searched the live web per query, every search would take hours. Instead:

> **Precompute a map from words to the documents containing them. Then answer
> every query by reading that map.**

Everything else in this project — crawling, indexing, TF-IDF, BM25, PageRank,
snippets — is infrastructure around that one sentence.

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

# index["python"] is now {1, 3} — no scanning required
```

That's a search engine. The rest is making it fast, accurate, and honest at
scale.

---

## 2. Offline and online

The pipeline splits into two halves running at completely different times, and
almost every design decision in the codebase follows from this split.

| | Offline | Online |
|---|---|---|
| **When** | before anyone searches | while a user waits |
| **How often** | occasionally | every request |
| **Budget** | minutes | milliseconds |
| **Does** | crawl, parse, index, PageRank | tokenize, look up, score, snippet |

When you search Google and it says "About 2,340,000 results (0.42 seconds)",
that 0.42 seconds is not Google reading the web. It's Google reading an index
built days or weeks ago.

You can see the same shape in `server.py`. At startup it loads the index,
computes PageRank over the whole link graph, computes average document length,
and derives suggestions. A request then does *none* of that — it tokenizes a few
words, reads a few dictionary keys, and scores a handful of candidates.

**Search isn't fast because the work is fast. It's fast because the work already
happened.**

---

## 3. Tokenization

Before anything can be matched, text has to be reduced to comparable units.

```
"Best PYTHON web-development!"  →  ['best', 'python', 'web', 'development']
```

Three things happened: lowercasing, splitting on non-alphanumerics, discarding
punctuation. A "token" is one indexable word.

### The symmetry rule

**The same function must process documents and queries.**

If a page contains `"Python!"` and you search `python`, those match only if both
sides were normalized the same way. This is why `query.py` is three lines that
just call the parser's `tokenize`. It's not indirection for its own sake — it
makes the symmetry structural. Any future change (stemming, synonyms, accent
folding) applies to both sides automatically.

Break this rule and searches silently return nothing. There's no error message
for "your query was normalized differently than your documents".

### What we give up

Splitting `web-development` into two tokens means we can't distinguish the
phrase `"web development"` from a page mentioning `web` in one paragraph and
`development` in another. Real engines fix this with a **positional index** —
storing not just *how often* a term appears but *at which positions* — which is
what makes quoted phrase search possible.

---

## 4. The inverted index

The natural way to store documents is document → words. The inverted index
flips it: **word → documents**.

```
Documents:
  1: "cats are friendly"
  2: "dogs are friendly"
  3: "cats and dogs"

Inverted index:
  cats     → {1, 3}
  dogs     → {2, 3}
  friendly → {1, 2}
  are      → {1, 2}
  and      → {3}
```

Searching `cats` is now a dictionary lookup returning `{1, 3}` — no document was
examined.

Our version stores counts too:

```python
index["python"] = {0: 1, 2: 1, 4: 6, 5: 1}
```

Document 4 contains "python" six times. Those counts are what ranking runs on.

**Why this matters at scale:** with a billion documents, scanning is impossible
and lookup is instant. The index is bigger than the documents it describes, and
that trade — space for time — is one every search engine makes.

---

## 5. Retrieval

Retrieval answers a cheap question: *which documents could possibly match?*

We take the **union** of the postings lists — a document qualifies if it
contains **any** query term:

```
query "python web"
  python → {0, 2, 4, 5}
  web    → {1, 2, 3, 4, 5}
  union  → {0, 1, 2, 3, 4, 5}
```

All six documents. Retrieval deliberately does no scoring — it returns them in
doc-ID order, which is meaningless as a ranking. Its only job is to shrink "the
whole corpus" down to "documents worth thinking about".

### OR vs AND

Requiring *all* terms (AND) gives sharper results but falls off a cliff — one
unusual word and you get zero results. OR degrades gracefully, and hands the
ordering problem to ranking, where a document containing both terms scores
higher anyway.

Google behaves roughly this way: it prefers all your terms but will drop some
rather than show you nothing, which is why results sometimes show a word
struck through.

---

## 6. Ranking, part 1: TF-IDF

Retrieval said *which*. Ranking says *which is best*. This is where a search
engine earns its keep.

TF-IDF combines two intuitions.

### TF — term frequency

**"How much is this document about this word?"**

A page mentioning "python" six times is more about Python than one mentioning it
once. But raw counts favor long documents, so we divide by length:

```
TF = count / document_length
```

Without the division, a 10,000-word page mentioning "python" twice would
outrank a focused 100-word page mentioning it twice. **Hold onto this
division — it's what breaks later.**

### IDF — inverse document frequency

**"How much does this word narrow things down?"**

Not all words are equally useful. "database" appearing in 1 of 6 documents is
enormously informative. "the" appearing in all 6 tells you nothing.

```
IDF = log(N / df)
```

where `df` is how many documents contain the term. On our 6-page corpus:

| term | df | log(6/df) | interpretation |
|---|---|---|---|
| `database` | 1 | **1.792** | very informative |
| `python` | 4 | 0.405 | moderately informative |
| `web` | 5 | 0.182 | weak signal |
| `the` | 6 | **0.000** | worthless |

That bottom row is the beautiful part. **A word in every document scores exactly
zero and contributes nothing.** We never wrote a stopword list — the math
discovered which words are noise.

### Why a logarithm?

Without it, a word in 1 of 1,000,000 documents would be a million times more
important than one in 1 of 1. That's too aggressive; rarity has diminishing
returns. The log compresses the scale so going from "in 1000 docs" to "in 100
docs" matters about as much as "in 100" to "in 10".

### Putting them together

```
score(doc) = Σ  TF(term, doc) × IDF(term)
           terms
```

**"This page uses the query's rare words, a lot."**

Worked example — `python web` against `python.html` (108 tokens, "python" ×6,
"web" ×2):

```
python:  TF = 6/108 = 0.0556    IDF = 0.405    → 0.0225
web:     TF = 2/108 = 0.0185    IDF = 0.182    → 0.0034
                                        total  = 0.0259
```

That 0.0259 was the top score on the corpus, and python.html ranked #1. TF-IDF
worked exactly as advertised.

---

## 7. Where TF-IDF broke

Then we crawled 40 real pages, and it fell apart immediately.

Searching `einstein` on the crawl returned three tiny tag pages tied at the top.
The actual **Albert Einstein page ranked #4.**

Here is the whole story in two rows:

| page | "einstein" count | length | TF | × IDF (0.916) |
|---|---|---|---|---|
| `/author/Albert-Einstein` | **12** | 650 | 12/650 = 0.01846 | **0.0169** |
| `/tag/adulthood/page/1/` | **1** | 53 | 1/53 = 0.01887 | **0.0173** |

A page mentioning Einstein **once** in passing beat the page mentioning him
**twelve times**.

### Why

The culprit is `TF = count / length` — the division that was *correct and
necessary* on the small corpus.

It assumes relevance scales **linearly and inversely** with length: that a page
twice as long must contain a term twice as often to be equally about it. That
isn't how writing works. A 650-word article about Einstein doesn't need 12× the
mentions of a 53-word stub to be more about Einstein. It just needs to be about
Einstein.

The same bug hit `love`: `/tag/friendship/` (6 mentions in 172 tokens) beat
`/tag/love/` (23 mentions in 712 tokens).

### Why it was invisible before

Our six hand-written pages were 77, 97, 100, 100, 107, and 108 tokens. Almost
identical. **Length normalization had nothing to bite on**, so the flaw couldn't
express itself.

The crawl ranged from 17 to 712 tokens, and the flaw surfaced on the first
realistic query. This is a good lesson on its own: *a test corpus that's too
uniform will hide your bugs.*

---

## 8. Ranking, part 2: BM25

BM25 ("Best Match 25", the 25th formula in a research series from the 1990s) is
the standard successor to TF-IDF. It's what Lucene, Elasticsearch, and
Solr use by default, and it fixes exactly the failure above.

```
                    f · (k1 + 1)
score = Σ  IDF(t) · ────────────────────────────────
       terms         f + k1 · (1 - b + b · len/avgdl)
```

Intimidating, but it's TF with two corrections. Take them one at a time.

### Correction 1: saturation

Look at just the term-frequency part, for a document of average length:

```
f · (k1 + 1)
────────────      with k1 = 1.5
   f + k1
```

| occurrences (f) | value |
|---|---|
| 1 | 1.00 |
| 2 | 1.43 |
| 3 | 1.67 |
| 5 | 1.92 |
| 12 | 2.22 |
| 100 | 2.46 |
| ∞ | **2.50** |

It climbs steeply, then **flattens**. The 2nd mention adds 43%. The 100th adds
essentially nothing. The curve approaches `k1 + 1` and stops.

This matches how relevance actually works. A page mentioning "einstein" twice
instead of once is meaningfully more about Einstein. A page mentioning it 100
times instead of 99 is not — it's probably spam.

That's the second purpose of saturation: back when pages were written to game
search engines, **keyword stuffing** (repeating a term hundreds of times) was a
standard trick. Linear TF rewards it without limit. BM25 caps the payoff.

**`k1` controls how fast this flattens.** Lower = saturates sooner. At `k1 = 0`
only presence matters, not count at all.

### Correction 2: partial length normalization

```
1 - b + b · (len / avgdl)          with b = 0.75
```

This is the denominator's length correction, and `b` interpolates between two
extremes:

| b | behavior |
|---|---|
| **1.0** | `len/avgdl` — full normalization. This is TF-IDF's behavior, the one that broke. |
| **0.0** | `1` — length ignored entirely. Long documents win by bulk. |
| **0.75** | three-quarters of the way to full. Long documents are discounted, but not annihilated. |

Note that length is measured **against the corpus average**, not absolutely. 650
tokens is long among tag stubs and short among journal articles; only the ratio
means anything.

For an average-length document `len/avgdl = 1`, so the correction is exactly 1
regardless of `b`. It only bites as documents deviate from average.

### The same case, recomputed

Corpus: 40 documents, average length **208** tokens, `k1 = 1.5`, `b = 0.75`,
IDF(einstein) = 0.910.

**Albert Einstein page** — 12 occurrences, 650 tokens:

```
normalized = 1 - 0.75 + 0.75 × (650/208) = 0.25 + 2.344 = 2.594
tf factor  = 12 × 2.5 / (12 + 1.5 × 2.594) = 30 / 15.891 = 1.888
score      = 1.888 × 0.910 = 1.718
```

**Tag stub** — 1 occurrence, 53 tokens:

```
normalized = 1 - 0.75 + 0.75 × (53/208) = 0.25 + 0.191 = 0.441
tf factor  = 1 × 2.5 / (1 + 1.5 × 0.441) = 2.5 / 1.662 = 1.505
score      = 1.505 × 0.910 = 1.369
```

**1.718 vs 1.369.** The Einstein page wins clearly. Under TF-IDF it was 0.0169
vs 0.0173 and *lost*.

(The engine prints 1.719 and 1.370 — it carries full precision instead of the
rounded intermediates shown here.)

The long page is still discounted — its 12 mentions don't give it 12× the score
— but it's no longer punished so hard that a stub with one passing mention
beats it.

### The IDF changed too

BM25 uses a smoothed IDF:

```
IDF = log(1 + (N - df + 0.5) / (df + 0.5))
```

The textbook probabilistic form omits the `1 +` and goes **negative** once a term
appears in more than half the corpus — meaning common words would actively
subtract from a document's score. The `1 +` prevents that. Lucene uses this
variant.

**The tradeoff, stated honestly:** this costs us the elegant property from
earlier. A word in every document now scores 0.074 instead of exactly 0.000.

| term | df (of 6) | classic IDF | BM25 IDF |
|---|---|---|---|
| `the` | 6 | **0.000** | 0.074 |
| `web` | 5 | 0.182 | 0.241 |
| `python` | 4 | 0.405 | 0.442 |
| `database` | 1 | 1.792 | 1.540 |

In practice it barely matters — `database` still outweighs `the` by 21×, so
stopwords contribute almost nothing to a real mixed query. But a *pure* stopword
query like `the and` now produces an arbitrary frequency-based ordering instead
of cleanly falling back to authority.

Both scorers are kept in `ranking.py` so you can see this yourself:

```bash
uv run python ranking.py einstein love
```

---

## 9. PageRank

TF-IDF and BM25 read the words **inside** a page. They have no way to tell a
carefully researched article from a page that merely uses the right vocabulary.

PageRank — the algorithm Larry Page and Sergey Brin built Google on — never
looks at the text at all. It asks a completely different question:

> **Who links to this page?**

### The circular definition

> A page is important if important pages link to it.

That's circular, and the circularity is the point. It can't be evaluated
directly, so we resolve it by **iteration**:

1. Assume every page is equally important.
2. Each page gives its score to the pages it links to.
3. Repeat until the numbers stop moving.

They converge. The scores that survive are the fixed point of the link graph.

### The random surfer

The cleanest way to understand PageRank: imagine someone browsing at random.
They land on a page, click a random link, click again, forever. **A page's
PageRank is the probability of finding this surfer there at any given moment.**

That's why our scores sum to exactly 1.0 — they're a probability distribution.

### Links are divided, not copied

```python
share = damping * scores[doc_id] / len(targets)
```

A page linking to 2 others gives each half its authority. A page linking to 100
gives each a hundredth.

**This is the anti-spam heart of PageRank.** You can't manufacture authority by
linking to everything — you only dilute what you have. An endorsement means
less when it's handed out freely.

### Damping (d = 0.85)

Real people don't click forever. 15% of the time our surfer gets bored and jumps
to a random page:

```
score = (1 - d)/N  +  d × (score arriving via links)
        └─ teleport ─┘   └────── link-following ──────┘
```

Without teleportation, score gets permanently trapped. Two pages linking only to
each other would accumulate everything and never give it back — a "rank sink".
The 15% escape hatch guarantees the whole graph stays reachable.

0.85 is the value from the original 1998 paper and has stuck.

### Dangling nodes

A page with **no outbound links** has score to give and nowhere to put it. Left
alone, that score vanishes each round, and eventually all score drains away.

So we collect it and redistribute it evenly:

```python
dangling_score = sum(scores[d] for d, targets in graph.items() if not targets)
base = (1 - damping)/num_docs + damping * dangling_score/num_docs
```

Our six hand-written pages all link to each other, so this branch never fired
for months. The first crawl brought in `https://example.com/` — which links only
to `iana.org`, off-host and never fetched — and it finally ran. It handled it
correctly: scores still summed to 1.0.

### Results on our corpus

```
0.2224  index.html             5 inbound
0.2028  databases.html         5 inbound
0.1794  web-development.html   4 inbound
0.1508  data-science.html      3 inbound
0.1437  python.html            3 inbound
0.1009  javascript.html        2 inbound
```

**Notice index.html and databases.html both have 5 inbound links but different
scores.** That's the whole difference between PageRank and counting links.
index.html's links come from better-connected pages, so they're worth more.

### Where PageRank goes wrong

On the real crawl, the **second-highest authority page was `/login`** — at
0.1143, more than 4× the uniform share of 0.025. Not because anyone considers
the login page authoritative, but because **every page links to it** in the site
navigation.

This is a genuine weakness. PageRank can't distinguish an editorial endorsement
from boilerplate chrome. Real engines fight it with boilerplate detection,
`rel="nofollow"`, and template analysis.

The homepage has the same issue in milder form: crawls seeded at the root give
it 0.2070, **8× the uniform share**, largely for being the root.

---

## 10. Combining two signals

We now have two scores per document, measuring different things:

- **Relevance** (BM25) — is this page about your query?
- **Authority** (PageRank) — do other pages treat it as worth pointing at?

They live on incompatible scales — BM25 around 0.5–2.0, PageRank around
0.02–0.22. Adding them raw would let the numerically larger one dominate by
accident. So we normalize each to 0–1, then take a weighted average:

```
final = (1 - w) × relevance + w × authority        with w = 0.2
```

### Why the weight must stay low

**Authority doesn't know what you searched for.** It's the same number for every
query. Push `w` too high and the most-linked page wins everything.

We can watch the tipping point directly. Searching `einstein` on the crawl:

| w | #1 result |
|---|---|
| 0.0 | `/author/Albert-Einstein` |
| 0.1 | `/author/Albert-Einstein` |
| **0.2** | **`/` (the homepage)** |
| 0.4 | `/` (the homepage) |

At 0.2 — our current setting, tuned on the small corpus where authority was
evenly spread — the homepage's structural advantage overwhelms a page that's
genuinely, specifically about Einstein.

**Authority should be a tiebreaker between relevant pages, not a substitute for
being relevant.**

### A caveat about the numbers

Relevance is normalized **across this query's candidates**, so the top result
always scores 1.0 before blending — no matter how good it actually is. Scores
are comparable *within* one result list, never *across* queries. Fine for
ordering; never show that number to a user as a "match percentage".

---

## 11. Snippets

A ranked list of URLs isn't a search engine yet. The snippet is what lets you
decide whether to click.

The problem isn't "summarize this page" — it's **"find the densest patch of
query terms and quote it"**. A snippet for `python` should land on the sentence
about Python, not the first 30 words of the page.

Our approach: slide a 30-token window over the document, score each by how many
query terms it covers, and quote the best one.

The scoring has a deliberate bias:

```python
score = distinct * 100 + total
```

**Distinct terms dominate raw count, by 100×.** A window containing `python` and
`web` beats one containing `python` five times. Matching more of what was asked
beats matching one part emphatically.

One subtlety worth noting: we quote the **original text**, not the tokens. Tokens
are lowercased and stripped of punctuation — fine for matching, unreadable for
humans. This is why the document store keeps `text` alongside `tokens`.

And the backend returns *segments* (`[{text, match}]`), not HTML with `<b>` tags.
Two reasons: React would need `dangerouslySetInnerHTML` to render server markup,
discarding its built-in escaping; and **document text is untrusted input** the
moment a crawler starts fetching real pages.

---

## 12. Crawling

Every other module is a pure function over data we already have. The crawler
touches the network, where every step can fail, be slow, lie about its content
type, or belong to someone who'd rather we didn't.

Most of `crawler.py` is restraint, not downloading.

### The traversal

Breadth-first: keep a queue (the **frontier**), pop a URL, fetch it, append its
links to the back. BFS rather than depth-first because it stays near the seed
pages — depth-first wanders down one branch and never comes back.

### The rules

**`robots.txt`** — sites publish which paths crawlers may touch. Nothing
technical enforces it. Honoring it is the entire social contract that makes
crawling acceptable rather than hostile.

**Crawl delay** — a crawler is a loop with no natural pause. Without a delay, a
small site experiences a burst indistinguishable from an attack. We wait 1s
between requests *to the same host*, and honor a longer delay if the site asks.

**Honest User-Agent** — so an administrator reading their logs can tell what
visited and block it if they want.

**Bounded work** — a page cap and a size cap. An unbounded crawler on a site
with generated URLs (calendars, search pages, infinite pagination) never
finishes.

### Two kinds of duplicate

This is the most interesting problem crawling surfaces, and it took us two
separate mechanisms.

**Syntactic duplicates** — different URL strings, same page by definition:

```
https://example.com/a#section     → fragment is a scroll position
HTTP://Example.COM:80/a           → scheme/host case, default port
https://example.com/a?utm_source=x → campaign tag, not a different page
```

All collapse to `https://example.com/a` via `normalize_url()`.

**Semantic duplicates** — genuinely different URLs serving identical content:

```
https://quotes.toscrape.com/tag/love/
https://quotes.toscrape.com/tag/love/page/1/
```

**No amount of URL canonicalization fixes this.** They're different URLs. Only
the content reveals they're the same page. So we hash the visible text and skip
anything we've already stored.

Our 40-page crawl contained three such pairs. Left in, they cost twice:

1. **Duplicate results** — the same page occupying two of the top ten slots.
2. **Split authority** — PageRank divided between two spellings, so the page
   ranked *lower* than a single copy would have.

Real engines go further with SimHash or shingling to catch *near*-duplicates
(same article, different ads), and prefer a page's declared `rel="canonical"`
URL when one exists.

---

## 13. What Google does that we don't

Zoogle implements the real architecture. It is missing essentially all of the
scale and most of the sophistication. Honestly:

### Scale

Google's index is hundreds of billions of pages across thousands of machines.
Ours is one `index.json` in memory. At web scale the index is **sharded** across
machines, each holding a slice; a query fans out to all of them, each returns
its best candidates, and results are merged. Postings lists are compressed to
bits per entry, not JSON.

### Learned ranking

We use two hand-tuned signals with a hand-picked weight. Google uses **hundreds
of signals** combined by machine-learned models trained on enormous amounts of
click and satisfaction data. BM25 and PageRank still exist in that stack — as
*features*, not as the final answer.

### Understanding the query

We tokenize and match literally. Google does:

- **Synonyms** — `car` also matches `automobile`
- **Stemming** — `running` matches `run`
- **Spelling correction** — the "did you mean" path
- **Entity recognition** — knowing "Albert Einstein" is a person
- **Intent** — recognizing that `weather` wants a widget, not documents
- **Semantic matching** (BERT/MUM) — matching *meaning*, not just words

That last one is the biggest gap. Our engine cannot match a page about "dogs"
to a query for "puppies" unless the literal word appears.

### Phrases and positions

We store *how often* a term appears. Real indexes store *where*, which enables
quoted phrase search and proximity scoring (terms near each other score higher
than terms scattered across a page).

### Personalization and context

Location, language, search history, device. A search for "football" means
different things in Dallas and Manchester. We have one global ranking.

### Freshness

For news, recency is a ranking signal. We have no notion of time at all —
`crawled/` is a snapshot with no dates.

### Spam and quality

An enormous adversarial industry exists to manipulate rankings. Google runs link
spam detection, content quality classifiers, and manual actions. We honor
`robots.txt` and hope for the best. Our `/login` page ranking second in authority
is exactly the kind of artifact a real engine has to defend against — and that's
without anyone attacking us.

### What we *did* get right

The architecture is genuinely the same shape:

- Crawl offline, index offline, answer queries from the index
- An inverted index as the core data structure
- Term-frequency-based relevance with document-length normalization
- A link-graph authority signal, blended with relevance
- Duplicate detection at both the URL and content level
- Snippets built from query-term positions
- `robots.txt` compliance and crawl politeness

That's not a toy. It's a real search engine, small.

---

## 14. Glossary

**avgdl** — average document length across the corpus. BM25's yardstick for
whether a given document is long.

**BM25** — "Best Match 25". The standard relevance function, adding term-count
saturation and tunable length normalization to TF-IDF's ideas.

**Corpus** — the collection of documents being searched.

**Damping factor (d)** — in PageRank, the probability the random surfer follows
a link rather than teleporting. Conventionally 0.85.

**Dangling node** — a page with no outbound links. Its score must be
redistributed or it leaks out of the system.

**df (document frequency)** — how many documents contain a term. Equal to the
length of its postings list.

**Frontier** — the crawler's queue of URLs still to visit.

**IDF (inverse document frequency)** — how rare, and therefore how informative, a
term is. `log(N/df)` classically.

**Inverted index** — a map from term → documents containing it. The opposite of
the natural document → terms direction, and the core data structure of search.

**k1** — BM25's saturation parameter. Controls how fast repeated occurrences
stop adding value. Typically 1.5.

**b** — BM25's length normalization parameter, from 0 (ignore length) to 1
(fully divide by it). Typically 0.75.

**PageRank** — authority scoring from the link graph alone. A page is important
if important pages link to it.

**Postings list** — the set of documents containing a given term, with counts.

**Random surfer** — the mental model behind PageRank: someone clicking links at
random forever. PageRank is the probability of finding them on a given page.

**Rank sink** — a group of pages linking only to each other, which would
accumulate all score without damping.

**Retrieval** — cheaply narrowing the corpus to candidate documents, before the
expensive scoring step.

**Snippet** — the excerpt under a result, chosen to show query terms in context.

**Stopword** — an extremely common word (`the`, `and`) carrying little meaning.
IDF handles these automatically rather than requiring a list.

**TF (term frequency)** — how often a term appears in a document, usually
normalized by document length.

**TF-IDF** — term frequency × inverse document frequency. The classic relevance
score: "uses the query's rare words, often".

**Tokenization** — splitting text into normalized, indexable units.

---

## Further reading

- **[The Anatomy of a Large-Scale Hypertextual Web Search Engine](http://infolab.stanford.edu/~backrub/google.html)** — Brin & Page, 1998. The
  original Google paper. Remarkably readable, and you'll recognize most of it now.
- **[Introduction to Information Retrieval](https://nlp.stanford.edu/IR-book/)** — Manning, Raghavan & Schütze. The standard
  textbook, free online. Chapters 1, 6, and 21 map directly onto this project.
- **[The Probabilistic Relevance Framework: BM25 and Beyond](https://www.staff.city.ac.uk/~sbrp622/papers/foundations_bm25_review.pdf)** — Robertson & Zaragoza. Where BM25 comes from,
  by the people who built it.

---

*Companion document: [CODE.md](CODE.md) — every module and function explained.*
