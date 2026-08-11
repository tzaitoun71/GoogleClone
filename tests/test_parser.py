"""parser.py — tokenizing, URL canonicalization, and HTML extraction.

The two functions that matter most here are tokenize() and normalize_url().
Both are "agreement" functions: their value comes from being applied
identically in more than one place, so the tests are mostly about pinning that
agreement down.
"""

from parser import normalize_url, parse_file, parse_html, tokenize
from query import process_query


class TestTokenize:
    def test_lowercases_and_drops_punctuation(self):
        assert tokenize("Best PYTHON web-development!") == [
            "best",
            "python",
            "web",
            "development",
        ]

    def test_keeps_digits_and_alphanumeric_runs(self):
        assert tokenize("Python 3.12 and utf8") == ["python", "3", "12", "and", "utf8"]

    def test_empty_and_punctuation_only_yield_nothing(self):
        assert tokenize("") == []
        assert tokenize("!!! ??? ...") == []

    def test_drops_non_ascii_letters(self):
        # Documented limitation, not an accident: TOKEN_RE is [a-z0-9]+, so
        # accented characters are split out rather than folded. Pinned here so
        # that changing it has to be a deliberate decision.
        assert tokenize("café") == ["caf"]
        assert tokenize("Russell–Einstein") == ["russell", "einstein"]

    def test_query_and_document_tokenizers_are_the_same_function(self):
        # The single most important invariant in the project. If these ever
        # diverge, queries silently stop matching documents they should match.
        for raw in ["Python!", "  MIXED Case  ", "a-b-c", "utm_source=x"]:
            assert process_query(raw) == tokenize(raw)


class TestNormalizeUrl:
    def test_strips_fragment(self):
        assert normalize_url("http://e.com/a#section") == "http://e.com/a"

    def test_lowercases_scheme_and_host_but_not_path(self):
        assert normalize_url("HTTP://Example.COM/Path") == "http://example.com/Path"

    def test_drops_default_ports(self):
        assert normalize_url("http://e.com:80/a") == "http://e.com/a"
        assert normalize_url("https://e.com:443/a") == "https://e.com/a"

    def test_keeps_non_default_ports(self):
        assert normalize_url("http://e.com:8080/a") == "http://e.com:8080/a"

    def test_drops_tracking_params_but_keeps_real_ones(self):
        got = normalize_url("http://e.com/a?utm_source=x&q=1&fbclid=y")
        assert got == "http://e.com/a?q=1"

    def test_empty_path_becomes_root(self):
        assert normalize_url("http://e.com") == "http://e.com/"

    def test_relative_filename_passes_through_untouched(self):
        # This is what keeps the hand-written corpus/ working: those "URLs" are
        # bare filenames with no host, and must not acquire a leading slash.
        assert normalize_url("python.html") == "python.html"

    def test_is_idempotent(self):
        # The crawler asks "have I fetched this?" and the parser canonicalizes
        # every link it extracts. Both must land on the same string, and stay
        # there however many times it is applied.
        for url in [
            "HTTP://Example.COM:80/Path?utm_source=x&q=1#frag",
            "https://e.com",
            "python.html",
        ]:
            once = normalize_url(url)
            assert normalize_url(once) == once


class TestParseHtml:
    def test_prefers_title_tag(self):
        doc = parse_html("<html><head><title> Hi </title></head></html>", "a.html")
        assert doc["title"] == "Hi"

    def test_falls_back_to_h1_when_no_title(self):
        doc = parse_html("<html><body><h1>Heading</h1></body></html>", "a.html")
        assert doc["title"] == "Heading"

    def test_title_is_empty_when_neither_exists(self):
        assert parse_html("<html><body><p>x</p></body></html>", "a.html")["title"] == ""

    def test_script_and_style_never_reach_text_or_tokens(self):
        # A contract test, not a guard on the decompose() call: BeautifulSoup
        # 4.9+ already excludes Script/Stylesheet strings from get_text(), so
        # this passes with or without that line. It is still worth keeping —
        # it is what would catch a bs4 upgrade or a switch to lxml quietly
        # changing the behaviour we rely on.
        html = """
            <html><body>
              <script>var secret = 'alert';</script>
              <style>.cls { color: red; }</style>
              <p>Real content</p>
            </body></html>
        """
        doc = parse_html(html, "a.html")
        assert "secret" not in doc["text"]
        assert "alert" not in doc["tokens"]
        assert "color" not in doc["tokens"]
        assert doc["tokens"] == ["real", "content"]

    def test_resolves_relative_links_against_the_page_url(self):
        doc = parse_html('<a href="b.html">b</a>', "http://e.com/dir/a.html")
        assert doc["links"] == ["http://e.com/dir/b.html"]

    def test_deduplicates_repeated_links(self):
        # A page linking to the same target twice isn't two endorsements.
        html = '<a href="b.html">one</a><a href="b.html">two</a>'
        assert parse_html(html, "a.html")["links"] == ["b.html"]

    def test_links_are_normalized_on_the_way_out(self):
        doc = parse_html('<a href="http://E.com/b?utm_source=x">b</a>', "a.html")
        assert doc["links"] == ["http://e.com/b"]

    def test_tokens_match_tokenizing_the_text(self):
        doc = parse_html("<p>Alpha, Beta!</p>", "a.html")
        assert doc["tokens"] == tokenize(doc["text"])


class TestParseFile:
    def test_falls_back_to_filename_when_no_url_given(self, corpus_dir):
        doc = parse_file(corpus_dir / "alpha.html")
        assert doc["url"] == "alpha.html"

    def test_uses_the_supplied_url_when_there_is_one(self, corpus_dir):
        doc = parse_file(corpus_dir / "alpha.html", url="http://e.com/alpha")
        assert doc["url"] == "http://e.com/alpha"
