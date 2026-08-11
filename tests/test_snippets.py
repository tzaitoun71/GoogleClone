"""snippets.py — choosing and marking up the excerpt under a result."""

from snippets import ELLIPSIS, highlight, make_snippet


class TestMakeSnippet:
    def test_lands_on_the_query_terms_not_the_start_of_the_page(self):
        text = "Filler. " * 60 + "The python section is here. " + "Filler. " * 60
        assert "python" in make_snippet(text, ["python"])

    def test_prefers_covering_more_distinct_terms(self):
        # One passage repeats a single term; the other has both. Covering more
        # of what was asked for should win over matching one part emphatically.
        text = (
            "python python python python python. "
            + "padding " * 40
            + "python and web together. "
            + "padding " * 40
        )
        snippet = make_snippet(text, ["python", "web"])
        assert "web" in snippet

    def test_quotes_the_original_text_not_the_tokens(self):
        # Real capitalization and punctuation come back, which is why the doc
        # store keeps `text` alongside `tokens`.
        text = "Alpha. PYTHON, the language! Omega."
        assert "PYTHON," in make_snippet(text, ["python"])

    def test_falls_back_to_the_opening_when_no_term_appears(self):
        text = "Alpha beta gamma delta epsilon."
        snippet = make_snippet(text, ["absent"])
        assert snippet.startswith("Alpha")

    def test_empty_text_yields_empty_snippet(self):
        assert make_snippet("", ["python"]) == ""

    def test_text_without_any_tokens_yields_empty_snippet(self):
        assert make_snippet("!!! ???", ["python"]) == ""

    def test_short_text_gets_no_ellipsis(self):
        text = "Alpha beta gamma."
        snippet = make_snippet(text, ["beta"], width=30)
        assert not snippet.startswith(ELLIPSIS)
        assert not snippet.endswith(ELLIPSIS)

    def test_truncated_text_is_marked_with_ellipsis(self):
        text = "word " * 200
        snippet = make_snippet(text, ["word"], width=10)
        assert snippet.endswith(ELLIPSIS)

    def test_collapses_whitespace_inherited_from_html(self):
        text = "Alpha\n\n   beta\t\tgamma"
        assert "\n" not in make_snippet(text, ["beta"])
        assert "  " not in make_snippet(text, ["beta"])

    def test_respects_the_width(self):
        text = "word " * 200
        snippet = make_snippet(text, ["word"], width=5)
        # Five tokens plus the ellipsis markers, not the whole document.
        assert len(snippet.split()) < 10


class TestHighlight:
    def test_segments_reassemble_into_the_original_snippet(self):
        # The frontend renders these in order, so their concatenation has to be
        # exactly what was passed in — no characters gained or lost.
        snippet = "Alpha PYTHON, and web!"
        segments = highlight(snippet, ["python", "web"])
        assert "".join(s["text"] for s in segments) == snippet

    def test_marks_query_terms_case_insensitively(self):
        segments = highlight("A PYTHON here", ["python"])
        assert any(s["match"] and s["text"] == "PYTHON" for s in segments)

    def test_does_not_mark_other_words(self):
        segments = highlight("alpha beta", ["alpha"])
        assert all(not s["match"] for s in segments if "beta" in s["text"])

    def test_merges_adjacent_segments_of_the_same_kind(self):
        # The UI should get "the quick" rather than "the", " ", "quick".
        segments = highlight("the quick brown", ["nothing"])
        assert len(segments) == 1

    def test_returns_data_rather_than_markup(self):
        # Deliberate: document text is untrusted once a real crawler runs, so
        # the backend must never hand the frontend HTML to inject.
        segments = highlight("<script>alert(1)</script>", ["alert"])
        assert all(set(s) == {"text", "match"} for s in segments)
        assert "".join(s["text"] for s in segments) == "<script>alert(1)</script>"

    def test_empty_snippet_yields_no_segments(self):
        assert highlight("", ["python"]) == []
