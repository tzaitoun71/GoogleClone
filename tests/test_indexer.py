"""indexer.py — the inverted index and the doc store."""

import json

from indexer import build_index, load_index, save_index


class TestBuildIndex:
    def test_assigns_ids_deterministically_by_sorted_filename(self, documents):
        # sorted() in build_index means the same page always gets the same ID
        # across runs. Tests and debugging both depend on that.
        urls = [documents[i]["url"] for i in sorted(documents)]
        assert urls == ["alpha.html", "beta.html", "delta.html", "gamma.html"]

    def test_postings_record_term_frequency_per_document(self, index, documents):
        alpha = next(i for i, d in documents.items() if d["url"] == "alpha.html")
        gamma = next(i for i, d in documents.items() if d["url"] == "gamma.html")
        # "python" appears twice in alpha, once in gamma.
        assert index["python"][alpha] == 2
        assert index["python"][gamma] == 1

    def test_a_term_maps_only_to_documents_that_contain_it(self, index, documents):
        beta = next(i for i, d in documents.items() if d["url"] == "beta.html")
        assert beta not in index["python"]

    def test_length_is_the_token_count(self, index, documents):
        for doc_id, doc in documents.items():
            from_index = sum(
                freq
                for postings in index.values()
                for d, freq in postings.items()
                if d == doc_id
            )
            assert doc["length"] == from_index

    def test_doc_store_keeps_what_later_stages_need(self, documents):
        for doc in documents.values():
            assert set(doc) == {"url", "title", "text", "length", "links"}

    def test_missing_manifest_falls_back_to_filenames(self, documents):
        # No manifest.json in the fixture, so URLs are the file names.
        assert all(d["url"].endswith(".html") for d in documents.values())

    def test_manifest_supplies_real_urls_when_present(self, corpus_dir):
        (corpus_dir / "manifest.json").write_text(
            json.dumps({"alpha.html": "http://e.com/alpha"}), encoding="utf-8"
        )
        documents = build_index(str(corpus_dir))["documents"]
        urls = {d["url"] for d in documents.values()}
        assert "http://e.com/alpha" in urls
        # Pages absent from the manifest still fall back to their filename.
        assert "beta.html" in urls

    def test_empty_directory_produces_empty_structures(self, tmp_path):
        built = build_index(str(tmp_path))
        assert built == {"documents": {}, "index": {}}


class TestRoundTrip:
    def test_save_then_load_restores_integer_keys(self, built, tmp_path):
        # The subtle one. JSON has no integer keys, so a naive round trip turns
        # every doc ID into a string and every later lookup silently misses.
        path = tmp_path / "index.json"
        save_index(built, str(path))
        loaded = load_index(str(path))

        assert all(isinstance(k, int) for k in loaded["documents"])
        assert all(
            isinstance(d, int)
            for postings in loaded["index"].values()
            for d in postings
        )

    def test_round_trip_preserves_the_data(self, built, tmp_path):
        path = tmp_path / "index.json"
        save_index(built, str(path))
        assert load_index(str(path)) == built
