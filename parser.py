"""
  parser.py — turn a raw HTML page into a clean, structured document.

  This is the second stage of the pipeline. Input: one HTML file. Output: a dict
  the indexer and PageRank can consume. Everything here is about turning messy
  HTML into uniform, normalized data.
"""

import re
from pathlib import Path
from urllib.parse import urljoin, urldefrag

from bs4 import BeautifulSoup

# A "token" is one indexable word. We keep runs of letters and digits and drop
# everything else (punctuation, whitespace). Lowercasing happens before this.
TOKEN_RE = re.compile(r"[a-z0-9]+")

def tokenize(text: str) -> list[str]:
    """Normalize text into a flat list of tokens.
    The exact same function must be used on documents AND queries, so that
    "Python!" in a page and "python" in a query end up identical. That symmetry
    is the whole reason matching works.
    """
    return TOKEN_RE.findall(text.lower())

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
    # relative "python.html" becomes a full, comparable target. We also strip
    # the #fragment, since "page.html#top" and "page.html" are the same document.
    links = []
    for a in soup.find_all("a", href=True):
        target = urljoin(url, a["href"])
        target, _ = urldefrag(target) # discard the #fragment
        links.append(target)

    return {
        "url": url,
        "title": title,
        "text": text,
        "tokens": tokenize(text),
        "links": links,
    }

def parse_file(path: str | Path) -> dict:
    """Read an HTML file from disk and parse it.
    
    We use the file name (e.g. "python.html") as the URL for now. When we build
    the real crawler later, we'll pass in the actual http URL instead - the rest
    of the pipeline won't care which it was.
    """
    path = Path(path)
    html = path.read_text(encoding="utf-8")
    return parse_html(html, url=path.name)

if __name__ == "__main__":
      # Quick manual check: parse one page and print what came out.
      import json
      doc = parse_file("corpus/python.html")
      print(json.dumps(
          {**doc, "tokens": doc["tokens"][:12] + ["..."]},  # trim tokens for readability
          indent=2,
      ))