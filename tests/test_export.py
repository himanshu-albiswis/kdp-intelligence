"""Tests for CSV export.

Every cell is scraped or model-written text, so the classic spreadsheet
formula-injection attack applies: a book titled "=HYPERLINK(...)" must open
in Excel as text, not execute. OWASP's guidance is to prefix =, +, -, @
with an apostrophe.
"""

import csv
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import export


class TestToCsv:
    def test_writes_header_then_rows_in_column_order(self):
        out = export.to_csv([{"b": 2, "a": 1}], [("a", "A"), ("b", "B")])
        lines = out.strip().splitlines()
        assert lines[0] == "A,B"
        assert lines[1] == "1,2"

    def test_missing_keys_become_empty_cells_not_crashes(self):
        out = export.to_csv([{"a": 1}], [("a", "A"), ("b", "B")])
        assert out.strip().splitlines()[1] == "1,"

    def test_commas_quotes_and_newlines_survive_round_trip(self):
        row = {"t": 'He said "go", then\nleft'}
        out = export.to_csv([row], [("t", "T")])
        parsed = list(csv.reader(io.StringIO(out)))
        assert parsed[1][0] == 'He said "go", then\nleft'

    @pytest.mark.parametrize("payload", ["=HYPERLINK(1)", "+1+2", "-2+3", "@SUM(A1)"])
    def test_formula_injection_is_neutralised(self, payload):
        out = export.to_csv([{"t": payload}], [("t", "T")])
        parsed = list(csv.reader(io.StringIO(out)))
        assert parsed[1][0].startswith("'"), f"{payload!r} would execute in Excel"

    def test_ordinary_negative_numbers_are_not_mangled(self):
        out = export.to_csv([{"n": -5}], [("n", "N")])
        parsed = list(csv.reader(io.StringIO(out)))
        assert parsed[1][0] == "-5", "numeric values are not injection vectors"

    def test_nested_dicts_flatten_to_something_readable(self):
        out = export.to_csv([{"gap": {"score": 71.2, "verdict": "OPEN"}}],
                            [("gap.score", "Gap"), ("gap.verdict", "Verdict")])
        parsed = list(csv.reader(io.StringIO(out)))
        assert parsed[1] == ["71.2", "OPEN"]

    def test_lists_join_with_semicolons(self):
        out = export.to_csv([{"formats": ["Kindle", "Paperback"]}], [("formats", "Formats")])
        parsed = list(csv.reader(io.StringIO(out)))
        assert parsed[1][0] == "Kindle; Paperback"


class TestBundleTables:
    def test_a_research_bundle_offers_its_tables(self):
        bundle = {"keywords": [{"keyword": "x"}], "books": [{"asin": "B1"}],
                  "book_intel": [{"asin": "B1"}],
                  "category_intel": {"categories": [{"category": "C"}]}}
        names = set(export.available_tables(bundle))
        assert {"keywords", "books", "book_intel", "categories"} <= names

    def test_a_teardown_bundle_offers_rows(self):
        assert "teardown" in export.available_tables({"rows": [{"asin": "B1"}]})

    def test_a_discovery_bundle_offers_cards(self):
        assert "cards" in export.available_tables({"cards": [{"concept": "x"}]})

    def test_extracting_a_table_yields_csv_and_a_filename(self):
        bundle = {"seed": "air fryer", "keywords": [
            {"keyword": "air fryer cookbook", "demand_index": 62, "demand_band": "high",
             "total_results": 6000, "opportunity": 68}]}
        name, text = export.table_csv(bundle, "keywords")
        assert name.endswith(".csv") and "air-fryer" in name
        assert "air fryer cookbook" in text
        assert text.splitlines()[0].startswith("Keyword")

    def test_asking_for_a_table_the_bundle_lacks_raises_key_error(self):
        with pytest.raises(KeyError):
            export.table_csv({"keywords": []}, "teardown")
