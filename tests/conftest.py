"""Shared fixtures.

Everything here is built from scratch in a tmp directory. Nothing in the test
suite reads index.json or crawled/ — those are build artifacts of a particular
crawl, so a test that depended on them would start failing the day you re-crawl,
for reasons that have nothing to do with the code being wrong.
"""

import pytest

from indexer import build_index

# A four-page corpus with a link structure we control exactly:
#
#     alpha  -> beta, gamma        gamma is linked by everyone (authority)
#     beta   -> gamma
#     gamma  -> (nothing)          dangling node
#     delta  -> gamma, gamma       duplicate link, and a self-link
#
PAGES = {
    "alpha.html": """
        <html><head><title>Alpha</title></head>
        <body>
          <p>Python is a language. Python is readable.</p>
          <a href="beta.html">beta</a>
          <a href="gamma.html">gamma</a>
        </body></html>
    """,
    "beta.html": """
        <html><head><title>Beta</title></head>
        <body>
          <p>Databases store rows.</p>
          <a href="gamma.html">gamma</a>
        </body></html>
    """,
    "gamma.html": """
        <html><head><title>Gamma</title></head>
        <body><p>Python and databases together.</p></body></html>
    """,
    "delta.html": """
        <html><head><title>Delta</title></head>
        <body>
          <p>Nothing much here.</p>
          <a href="gamma.html">gamma</a>
          <a href="gamma.html">gamma again</a>
          <a href="delta.html">itself</a>
        </body></html>
    """,
}


@pytest.fixture
def corpus_dir(tmp_path):
    """A tiny on-disk corpus, written fresh for each test."""
    for name, html in PAGES.items():
        (tmp_path / name).write_text(html, encoding="utf-8")
    return tmp_path


@pytest.fixture
def built(corpus_dir):
    """The corpus above, run through the real indexer."""
    return build_index(str(corpus_dir))


@pytest.fixture
def documents(built):
    return built["documents"]


@pytest.fixture
def index(built):
    return built["index"]


@pytest.fixture
def make_corpus():
    """Hands tests the builder below, so they never have to import conftest."""
    return _synthetic


def _synthetic(lengths, counts, term="einstein"):
    """Build a documents/index pair directly, bypassing parsing.

    Ranking is arithmetic over lengths and term counts, so the fastest way to
    test it is to state those numbers outright instead of reverse-engineering
    HTML that happens to produce them.

        lengths  {doc_id: token count}
        counts   {doc_id: occurrences of `term`}
    """
    documents = {
        doc_id: {
            "url": f"doc{doc_id}.html",
            "title": f"Doc {doc_id}",
            "text": "",
            "length": length,
            "links": [],
        }
        for doc_id, length in lengths.items()
    }
    index = {term: {d: c for d, c in counts.items() if c}}
    return documents, index
