"""
crawler.py — fetch real pages over HTTP and save them for the indexer.

This is the first stage of the pipeline and the only one that touches the
network, which changes the character of the code completely. Every other module
is a pure function over data we already have; here, every single step can fail,
be slow, lie about its content type, or belong to someone who would rather we
didn't. So most of this file is not "how to download a page" — it's restraint.

The traversal itself is breadth-first: keep a queue of URLs to visit (the
"frontier"), pop one, fetch it, add the links it contains to the back of the
queue. BFS rather than depth-first because it stays near the seed pages —
depth-first tends to wander off down one branch and never come back.

The rules we hold ourselves to:

  robots.txt     Checked once per host, before the first fetch. Sites use it to
                 say which paths crawlers may touch. It is not enforced by
                 anything technical — honoring it is the entire social contract
                 that makes crawling acceptable.

  Crawl delay    We wait between requests TO THE SAME HOST. A crawler is a loop
                 with no natural pause in it; without a delay, a small site
                 sees a burst indistinguishable from an attack.

  Honest agent   A User-Agent naming the crawler, so an administrator reading
                 their logs can tell what visited and block it if they want.

  Bounded work   A page cap and a size cap. An unbounded crawler on a site with
                 generated URLs (calendars, search pages) never finishes.
"""

import hashlib
import json
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests

from parser import normalize_url, parse_html

USER_AGENT = "ZoogleBot/0.1 (educational search engine project)"
CRAWL_DELAY = 1.0          # seconds between requests to the same host
REQUEST_TIMEOUT = 10       # seconds before we give up on a response
MAX_PAGE_BYTES = 2_000_000 # skip anything larger; it isn't an article


class Robots:
    """Per-host robots.txt rules, fetched once and remembered.

    Caching matters more than it looks: without it we'd fetch robots.txt before
    every single page, doubling our traffic to a site whose rules haven't
    changed since the first request.
    """

    def __init__(self, user_agent: str = USER_AGENT):
        self.user_agent = user_agent
        self._parsers: dict[str, RobotFileParser | None] = {}

    def _parser_for(self, url: str) -> RobotFileParser | None:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"

        if host not in self._parsers:
            parser = RobotFileParser()
            parser.set_url(f"{host}/robots.txt")
            try:
                parser.read()
            except Exception:
                # No robots.txt, or it couldn't be fetched. The convention is
                # that absent rules mean "no restrictions" — a missing file is
                # not a refusal.
                parser = None
            self._parsers[host] = parser

        return self._parsers[host]

    def allowed(self, url: str) -> bool:
        parser = self._parser_for(url)
        return True if parser is None else parser.can_fetch(self.user_agent, url)

    def delay_for(self, url: str) -> float:
        """Honor a site's own Crawl-delay if it declares one, else use ours."""
        parser = self._parser_for(url)
        declared = parser.crawl_delay(self.user_agent) if parser else None
        return max(float(declared or 0), CRAWL_DELAY)


def slugify(url: str) -> str:
    """Turn a URL into a safe, readable, unique filename.

    Readable so you can eyeball what got crawled; unique so two different URLs
    can't overwrite each other. The trailing digits are a hash of the full URL,
    which is what guarantees the second property — "/a/index.html" and
    "/b/index.html" would otherwise both want to be "index.html".
    """
    parts = urlsplit(url)
    stem = f"{parts.netloc}{parts.path}".strip("/")
    safe = "".join(char if char.isalnum() or char in "-._" else "-" for char in stem)
    safe = safe.strip("-")[:80] or "index"

    # hashlib, NOT the built-in hash(): Python randomizes string hashing per
    # process, so hash() would give the same URL a different filename on every
    # run and re-crawling would duplicate the whole corpus instead of updating.
    digest = hashlib.sha1(url.encode()).hexdigest()[:6]
    if not safe.endswith(".html"):
        safe += ".html"
    return f"{safe[:-5]}-{digest}.html"


def fetch(url: str, session: requests.Session) -> str | None:
    """Download one page, returning its HTML or None if it isn't usable."""
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as error:
        print(f"    failed: {type(error).__name__}")
        return None

    if response.status_code != 200:
        print(f"    skipped: HTTP {response.status_code}")
        return None

    # A URL doesn't tell you what's behind it. Only fetching does — and a link
    # ending in .html can still return a PDF, an image, or JSON.
    content_type = response.headers.get("content-type", "")
    if "html" not in content_type.lower():
        print(f"    skipped: content-type {content_type or 'unknown'}")
        return None

    if len(response.content) > MAX_PAGE_BYTES:
        print(f"    skipped: {len(response.content)} bytes")
        return None

    return response.text


def crawl(
    seeds: list[str],
    max_pages: int = 25,
    output_dir: str = "crawled",
    same_host_only: bool = True,
    fresh: bool = False,
) -> dict[str, str]:
    """Breadth-first crawl from `seeds`. Returns {filename: url} for the indexer.

    `same_host_only` is the difference between crawling a site and crawling the
    web. Left on, the frontier stays inside the hosts you seeded it with; turned
    off, one link to a big site and the crawl never comes back to yours.

    Crawls ACCUMULATE by default: pages and manifest entries from earlier runs
    are kept, so you can build a corpus from several seeds over time. Because
    slugify() is deterministic, re-crawling a URL overwrites its own file rather
    than adding a duplicate. Pass `fresh=True` to start the directory empty.
    """
    output = Path(output_dir)
    output.mkdir(exist_ok=True)
    manifest_path = output / "manifest.json"

    if fresh:
        for stale in output.glob("*.html"):
            stale.unlink()
        manifest_path.unlink(missing_ok=True)

    # Load what previous runs saved. Dropping this on the floor would leave old
    # pages on disk with no record of the URL they came from — the indexer would
    # fall back to their slug filenames, and every link pointing at them would
    # stop resolving, silently flattening the whole link graph.
    manifest: dict[str, str] = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )

    robots = Robots()
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    frontier = deque(normalize_url(seed) for seed in seeds)
    allowed_hosts = {urlsplit(url).netloc for url in frontier}

    # Everything we've QUEUED, not everything we've fetched — a URL goes in here
    # the moment it's enqueued. Marking on fetch instead would let the same page
    # enter the frontier many times before the first copy came back.
    seen: set[str] = set(frontier)

    last_fetch: dict[str, float] = {}

    # Counts THIS run only. Using len(manifest) would make max_pages a cap on
    # the whole corpus, so a second crawl into a directory of 25 pages would
    # fetch nothing at all.
    fetched = 0

    while frontier and fetched < max_pages:
        url = frontier.popleft()
        host = urlsplit(url).netloc

        print(f"[{fetched + 1}/{max_pages}] {url}")

        if not robots.allowed(url):
            print("    skipped: disallowed by robots.txt")
            continue

        # Politeness, measured per host: waiting is only owed to the server we
        # just talked to, so crawling two hosts doesn't make us twice as slow.
        delay = robots.delay_for(url)
        elapsed = time.monotonic() - last_fetch.get(host, 0.0)
        if elapsed < delay:
            time.sleep(delay - elapsed)

        last_fetch[host] = time.monotonic()
        html = fetch(url, session)
        if html is None:
            continue

        filename = slugify(url)
        (output / filename).write_text(html, encoding="utf-8")
        manifest[filename] = url
        fetched += 1

        # parse_html already normalizes and de-duplicates the links it finds, so
        # what comes back is directly comparable to what's in `seen`.
        for link in parse_html(html, url)["links"]:
            if link in seen:
                continue
            if not link.startswith(("http://", "https://")):
                continue  # mailto:, javascript:, tel: — nothing to fetch
            if same_host_only and urlsplit(link).netloc not in allowed_hosts:
                continue
            seen.add(link)
            frontier.append(link)

    # Written next to the pages so the indexer can recover which URL each file
    # came from — the filename is a slug, and PageRank needs the real URL to
    # match links against.
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))

    print(f"\nFetched {fetched} pages; {output}/ now holds {len(manifest)}")
    print(f"{len(seen)} URLs seen, {len(frontier)} left in the frontier")
    return manifest


if __name__ == "__main__":
    import argparse

    cli = argparse.ArgumentParser(description="Crawl pages into a local corpus.")
    cli.add_argument("seeds", nargs="+", help="one or more URLs to start from")
    cli.add_argument("--max-pages", type=int, default=25)
    cli.add_argument("--output", default="crawled")
    cli.add_argument(
        "--any-host",
        action="store_true",
        help="follow links off the seed hosts (careful: the web is large)",
    )
    cli.add_argument(
        "--fresh",
        action="store_true",
        help="empty the output directory first instead of adding to it",
    )
    args = cli.parse_args()

    crawl(
        args.seeds,
        max_pages=args.max_pages,
        output_dir=args.output,
        same_host_only=not args.any_host,
        fresh=args.fresh,
    )
