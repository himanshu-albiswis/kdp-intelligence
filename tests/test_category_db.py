"""Category database — a browsable catalogue that grows with every scan.

BookBeam sells 45,000 browsable categories. A one-shot crawl of that from a
rate-limited residential IP is not honest to promise; a store that records
every category any scan observes, plus a bounded crawler of Amazon's
bestseller tree when a proxy allows, is. Fixture cut from
amazon.com/gp/bestsellers/digital-text/ on 2026-09-05: subcategory links are
/zgbs/digital-text/<node>, ranked items sit in data-client-recs-list JSON
with render.zg.rank.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import category_db

BESTSELLERS = (
    '<a href="/Best-Sellers-Kindle-Store-Kindle-Newsstand/zgbs/digital-text/3000678011/ref=zg_bs_nav_digital-text_1">Kindle Newsstand</a>'
    '<a href="/Best-Sellers-Kindle-Store-Nonfiction/zgbs/digital-text/157325011/ref=zg_bs_nav">Kindle Nonfiction</a>'
    '<a href="/Best-Sellers-Kindle-Store-Kindle-eBooks/zgbs/digital-text/154606011?ref=x">Amazon Best Sellers</a>'
    '<div class="p13n-desktop-grid" data-client-recs-list="[{&quot;id&quot;:&quot;B0DZJ53SVW&quot;,&quot;metadataMap&quot;:{&quot;render.zg.rank&quot;:&quot;1&quot;}},'
    '{&quot;id&quot;:&quot;B0FFNQ7W9J&quot;,&quot;metadataMap&quot;:{&quot;render.zg.rank&quot;:&quot;2&quot;}}]"></div>'
)


class TestParseBestsellerPage:
    def test_reads_subcategories_with_node_ids(self):
        page = category_db.parse_bestseller_page(BESTSELLERS, own_node="154606011")
        subs = {s["node"]: s["name"] for s in page["subcategories"]}
        assert subs["157325011"] == "Kindle Nonfiction"
        assert "154606011" not in subs, "the page's own node is not a child"

    def test_reads_the_ranked_asins_in_order(self):
        page = category_db.parse_bestseller_page(BESTSELLERS, own_node="154606011")
        assert [t["asin"] for t in page["top"]] == ["B0DZJ53SVW", "B0FFNQ7W9J"]
        assert page["top"][0]["rank"] == 1

    def test_an_empty_page_parses_to_nothing(self):
        page = category_db.parse_bestseller_page("", own_node="1")
        assert page["subcategories"] == [] and page["top"] == []


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield category_db.CategoryStore(os.path.join(tmp, "categories.db"))


class TestStore:
    def test_observed_categories_from_a_scan_are_recorded(self, store):
        store.record_observed([{"category": "Air Fryer Recipes", "best_observed_rank": 2,
                                "entry_sales_day": 14.0, "books_observed": 3}], niche="air fryer")
        assert store.count() == 1
        assert store.search("fryer")[0]["name"] == "Air Fryer Recipes"

    def test_repeat_observations_keep_the_best_entry_bar(self, store):
        store.record_observed([{"category": "X", "best_observed_rank": 5, "entry_sales_day": 20.0,
                                "books_observed": 1}], niche="a")
        store.record_observed([{"category": "X", "best_observed_rank": 1, "entry_sales_day": 9.0,
                                "books_observed": 2}], niche="b")
        row = store.search("X")[0]
        assert row["best_observed_rank"] == 1 and row["entry_sales_day"] == 9.0
        assert row["times_seen"] == 2

    def test_crawled_nodes_are_recorded_with_parents(self, store):
        store.record_node("157325011", "Kindle Nonfiction", parent="154606011", depth=1)
        row = store.search("Nonfiction")[0]
        assert row["node"] == "157325011" and row["parent"] == "154606011"

    def test_search_is_case_insensitive_and_ranked_by_times_seen(self, store):
        store.record_observed([{"category": "Cooking", "best_observed_rank": 3,
                                "entry_sales_day": 5.0, "books_observed": 1}], niche="a")
        store.record_observed([{"category": "Cooking", "best_observed_rank": 3,
                                "entry_sales_day": 5.0, "books_observed": 1}], niche="b")
        store.record_observed([{"category": "Cookbooks", "best_observed_rank": 3,
                                "entry_sales_day": 5.0, "books_observed": 1}], niche="c")
        names = [r["name"] for r in store.search("cook")]
        assert names[0] == "Cooking"

    def test_browse_lists_children_of_a_node(self, store):
        store.record_node("154606011", "Kindle eBooks", parent=None, depth=0)
        store.record_node("157325011", "Kindle Nonfiction", parent="154606011", depth=1)
        assert [c["node"] for c in store.children("154606011")] == ["157325011"]


class TestCrawler:
    def test_walks_the_tree_breadth_first_within_a_budget(self, store):
        pages = {
            "154606011": BESTSELLERS,
            "157325011": '<a href="/x/zgbs/digital-text/999/ref=y">Cooking</a>',
            "3000678011": "", "999": "",
        }
        fetched = []

        def fetch(url):
            node = url.rstrip("/").split("/digital-text/")[-1].split("/")[0].split("?")[0]
            fetched.append(node)
            class R: status = 200; body = pages.get(node, "")
            return R()

        crawled = category_db.crawl(store, fetch=fetch, root="154606011", max_pages=3)
        assert crawled <= 3 and len(fetched) <= 3
        assert store.search("Nonfiction")

    def test_a_blocked_page_stops_nothing_else(self, store):
        def fetch(url):
            class R: status = 503; body = ""
            return R()
        assert category_db.crawl(store, fetch=fetch, root="154606011", max_pages=2) == 0
