"""
  parser.py — turn a raw HTML page into a clean, structured document.

  This is the second stage of the pipeline. Input: one HTML file. Output: a dict
  the indexer and PageRank can consume. Everything here is about turning messy
  HTML into uniform, normalized data.
"""

import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urldefrag, urlsplit, urlunsplit

from bs4 import BeautifulSoup

# A "token" is one indexable word. We keep runs of letters and digits and drop
# everything else (punctuation, whitespace). Lowercasing happens before this.
TOKEN_RE = re.compile(r"[a-z0-9]+")

# Query parameters that identify a marketing campaign rather than a page. Two
# URLs differing only in these point at identical content, and keeping both
# would put the same page in the index twice.
TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid")

def tokenize(text: str) -> list[str]:
    """Normalize text into a flat list of tokens.
    The exact same function must be used on documents AND queries, so that
    "Python!" in a page and "python" in a query end up identical. That symmetry
    is the whole reason matching works.
    """
    return TOKEN_RE.findall(text.lower())

def normalize_url(url: str) -> str:
    """Reduce a URL to a canonical form, so the same page has exactly one name.

    This is the URL equivalent of tokenize(): the crawler uses it to decide
    "have I already fetched this?", and the parser uses it on every link it
    extracts. Both MUST agree, or the crawler will fetch a page under one
    spelling while PageRank looks for it under another and finds nothing.

    What it collapses:
        #fragment         same document, different scroll position
        HTTP://Example    scheme and host are case-insensitive; paths aren't
        :80 / :443        the default port for the scheme is implied
        ?utm_source=...   campaign tags that don't change the content
        example.com       an empty path means the root, "/"

    Relative names like "python.html" pass through untouched, which is what
    keeps the original hand-written corpus working.
    """
    url, _ = urldefrag(url)
    parts = urlsplit(url)

    scheme, netloc = parts.scheme.lower(), parts.netloc.lower()
    if (scheme == "http" and netloc.endswith(":80")) or (
        scheme == "https" and netloc.endswith(":443")
    ):
        netloc = netloc.rsplit(":", 1)[0]

    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(TRACKING_PARAMS)
    ]

    # Only force a "/" path on real URLs — a bare filename has no host and
    # should stay exactly as it is.
    path = parts.path or ("/" if netloc else "")
    return urlunsplit((scheme, netloc, path, urlencode(kept), ""))


def parse_html(html: str, url: str) -> dict:
    """Parse one HTML string into a structured document."""
    soup = BeautifulSoup(html, "html.parser")

    # Title: prefer the <title> tag, fall back to the first <h1>, else empty.
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    elif soup.h1:
        title = soup.h1.get_text(strip=True)

    # Script/style tags contain code, not readable content - drop them so their
    # contents never leak into our text or tokens.
    for tag in soup(["script", "style"]):
        tag.decompose()

    # Visible text, with tags collapsed to a single spaces.
    text = soup.get_text(separator=" ", strip=True)

    # Outbound links. We resolve each href against the page's own URL so that a
    # relative "python.html" becomes a full, comparable target, then canonicalize
    # it the same way the crawler canonicalizes what it fetches.
    links = []
    for a in soup.find_all("a", href=True):
        target = normalize_url(urljoin(url, a["href"]))
        if target and target not in links:   # a page linking twice isn't two links
            links.append(target)

    return {
        "url": url,
        "title": title,
        "text": text,
        "tokens": tokenize(text),
        "links": links,
    }

def parse_file(path: str | Path, url: str | None = None) -> dict:
    """Read an HTML file from disk and parse it.

    Without a `url` we fall back to the file name ("python.html"), which is what
    the hand-written corpus uses. Crawled pages pass the real http URL they were
    fetched from — the rest of the pipeline never cares which it was.
    """
    path = Path(path)
    html = path.read_text(encoding="utf-8")
    return parse_html(html, url=url or path.name)

if __name__ == "__main__":
      # Quick manual check: parse one page and print what came out.
      import json
      doc = parse_file("corpus/python.html")
      print(json.dumps(
          {**doc, "tokens": doc["tokens"][:12] + ["..."]},  # trim tokens for readability
          indent=2,
      ))