"""Tests for the single source of truth on BSR -> sales -> royalty maths."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kdp_estimates as est


class TestSalesPerDay:
    def test_returns_none_without_a_bsr(self):
        assert est.sales_per_day(None) is None

    def test_returns_none_for_nonsense_bsr(self):
        assert est.sales_per_day(0) is None
        assert est.sales_per_day(-5) is None

    def test_midpoint_matches_the_anchor_at_an_anchor_bsr(self):
        e = est.sales_per_day(10_000)
        assert e.mid == pytest.approx(15.0, rel=0.01)

    def test_interpolates_between_anchors(self):
        # 30k sits between the 10k (15/day) and 50k (3/day) anchors
        e = est.sales_per_day(30_000)
        assert 3.0 < e.mid < 15.0

    def test_better_rank_sells_more(self):
        assert est.sales_per_day(1_000).mid > est.sales_per_day(100_000).mid

    def test_estimate_is_a_band_not_a_point(self):
        e = est.sales_per_day(10_000)
        assert e.low < e.mid < e.high

    def test_confidence_degrades_in_the_long_tail(self):
        near = est.sales_per_day(5_000)
        far = est.sales_per_day(900_000)
        assert near.confidence == "medium"
        assert far.confidence == "low"

    def test_band_is_wider_where_confidence_is_lower(self):
        near = est.sales_per_day(5_000)
        far = est.sales_per_day(900_000)
        assert (far.high / far.low) > (near.high / near.low)

    def test_smaller_marketplaces_sell_less_at_the_same_rank(self):
        us = est.sales_per_day(10_000, marketplace="us")
        uk = est.sales_per_day(10_000, marketplace="uk")
        india = est.sales_per_day(10_000, marketplace="in")
        assert us.mid > uk.mid > india.mid

    def test_unknown_marketplace_falls_back_to_us_scale(self):
        assert est.sales_per_day(10_000, marketplace="zz").mid == pytest.approx(
            est.sales_per_day(10_000, marketplace="us").mid
        )

    def test_print_store_uses_its_own_curve(self):
        kindle = est.sales_per_day(10_000, store="kindle")
        print_book = est.sales_per_day(10_000, store="books")
        assert kindle.mid != print_book.mid


class TestBsrForSales:
    def test_inverts_the_curve(self):
        bsr = est.bsr_for_sales(15.0, store="kindle")
        assert bsr == pytest.approx(10_000, rel=0.05)

    def test_returns_none_for_zero_sales(self):
        assert est.bsr_for_sales(0) is None

    def test_round_trips_through_sales_per_day(self):
        original = 25_000
        sales = est.sales_per_day(original, marketplace="us").mid
        assert est.bsr_for_sales(sales) == pytest.approx(original, rel=0.05)


class TestRoyaltyPerSale:
    def test_seventy_percent_band_subtracts_a_size_based_delivery_fee(self):
        r = est.royalty_per_sale("ebook", 4.99, file_mb=2.0)
        assert r["royalty"] == pytest.approx(0.70 * 4.99 - 0.30, abs=0.01)
        assert r["plan"] == "70%"

    def test_delivery_fee_scales_with_file_size(self):
        small = est.royalty_per_sale("ebook", 4.99, file_mb=1.0)["royalty"]
        large = est.royalty_per_sale("ebook", 4.99, file_mb=5.0)["royalty"]
        assert small > large

    def test_below_the_band_drops_to_thirty_five_percent(self):
        r = est.royalty_per_sale("ebook", 0.99)
        assert r["royalty"] == pytest.approx(0.35 * 0.99, abs=0.01)
        assert r["plan"] == "35%"

    def test_above_the_band_drops_to_thirty_five_percent(self):
        assert est.royalty_per_sale("ebook", 14.99)["plan"] == "35%"

    def test_paperback_subtracts_printing_cost(self):
        r = est.royalty_per_sale("paperback", 12.99, pages=200)
        assert r["print_cost"] > 0
        assert r["royalty"] == pytest.approx(0.60 * 12.99 - r["print_cost"], abs=0.01)

    def test_paperback_priced_below_printing_cost_earns_nothing(self):
        r = est.royalty_per_sale("paperback", 3.00, pages=400)
        assert r["royalty"] == 0.0
        assert any("break-even" in n for n in r["notes"])

    def test_rejects_unknown_format(self):
        with pytest.raises(ValueError):
            est.royalty_per_sale("papyrus_scroll", 9.99)

    def test_returns_none_royalty_for_missing_price(self):
        assert est.royalty_per_sale("ebook", None)["royalty"] is None


class TestKindleUnlimitedIncome:
    def test_page_reads_pay_out_at_the_kenp_rate(self):
        assert est.ku_payout_per_read(300) == pytest.approx(300 * est.KENP_RATE, abs=0.01)

    def test_ku_income_is_zero_when_the_niche_has_no_ku_share(self):
        assert est.ku_monthly_income(sales_per_day=10, ku_share=0.0, kenp_pages=300) == 0.0

    def test_ku_income_grows_with_ku_share(self):
        low = est.ku_monthly_income(sales_per_day=10, ku_share=0.2, kenp_pages=300)
        high = est.ku_monthly_income(sales_per_day=10, ku_share=0.8, kenp_pages=300)
        assert high > low > 0


class TestOneSourceOfTruth:
    """The bug this module exists to make impossible: two royalty answers."""

    def test_dashboard_and_royalty_engine_agree_on_ebook_royalty(self):
        import kdp_intel_dashboard as dash
        from server import royalty

        for price in (2.99, 4.99, 7.99, 9.99, 0.99, 14.99):
            assert dash.kdp_royalty_per_sale(price) == royalty.royalty_per_sale(
                "ebook", price
            )["royalty"], f"royalty diverged at price {price}"

    def test_dashboard_and_royalty_engine_agree_on_the_bsr_curve(self):
        import kdp_intel_dashboard as dash
        from server import royalty

        for bsr in (100, 5_000, 50_000, 400_000):
            assert dash.est_sales_per_day(bsr) == pytest.approx(
                royalty.sales_per_day(bsr, "kindle")
            ), f"sales curve diverged at BSR {bsr}"


class TestNicheIncome:
    """The niche-level money roll-up, including the KU income that was ignored."""

    books = [
        {"bsr": 5_000, "price": 4.99},
        {"bsr": 20_000, "price": 3.99},
        {"bsr": 150_000, "price": 9.99},
    ]

    def test_counts_only_books_with_both_a_rank_and_a_price(self):
        books = self.books + [{"bsr": None, "price": 4.99}, {"bsr": 900, "price": None}]
        result = est.niche_income(books, ku_share=0.0)
        assert result["books_counted"] == 3

    def test_returns_a_range_not_a_point(self):
        r = est.niche_income(self.books, ku_share=0.0)
        assert r["total_low"] < r["total_mid"] < r["total_high"]

    def test_ku_share_adds_income_on_top_of_paid_sales(self):
        without = est.niche_income(self.books, ku_share=0.0)
        with_ku = est.niche_income(self.books, ku_share=0.6)
        assert with_ku["ku_mid"] > 0
        assert with_ku["total_mid"] > without["total_mid"]
        assert with_ku["paid_mid"] == pytest.approx(without["paid_mid"])

    def test_ku_income_is_reported_separately_so_it_can_be_audited(self):
        r = est.niche_income(self.books, ku_share=0.6)
        assert r["total_mid"] == pytest.approx(r["paid_mid"] + r["ku_mid"], abs=0.02)

    def test_empty_input_yields_zeroes_not_a_crash(self):
        r = est.niche_income([], ku_share=0.5)
        assert r["total_mid"] == 0.0
        assert r["books_counted"] == 0
        assert r["confidence"] == "none"

    def test_confidence_is_the_weakest_link(self):
        # a 900k BSR book is a low-confidence estimate; it drags the roll-up down
        mixed = est.niche_income([{"bsr": 5_000, "price": 4.99},
                                  {"bsr": 900_000, "price": 4.99}], ku_share=0.0)
        assert mixed["confidence"] == "low"

    def test_smaller_marketplace_yields_smaller_income(self):
        us = est.niche_income(self.books, ku_share=0.0, marketplace="us")
        india = est.niche_income(self.books, ku_share=0.0, marketplace="in")
        assert us["total_mid"] > india["total_mid"]


class TestCurrencyGuard:
    """Amazon.com serves INR to an Indian IP. Prices were parsed as dollars.

    Observed live on 2026-08-31 from a Bengaluru IP: amazon.com returned
    `a-price-symbol">INR` with wholes like 1,337 — and the tool reported
    `avg_buy_price: $1593.95` and computed USD royalties on it. Every money
    figure downstream was wrong, silently.
    """

    def test_reads_a_dollar_symbol(self):
        assert est.observed_currency(["$4.99", "$12.00"]) == "$"

    def test_reads_a_rupee_symbol(self):
        assert est.observed_currency(["₹1,337.00", "₹1,789.00"]) == "₹"

    def test_reads_a_three_letter_code(self):
        assert est.observed_currency(["INR 1,337.00"]) == "INR"

    def test_ignores_bare_numbers(self):
        assert est.observed_currency(["1337.00", "42"]) is None

    def test_empty_input_is_unknown(self):
        assert est.observed_currency([]) is None

    def test_takes_the_most_common_symbol(self):
        assert est.observed_currency(["$4.99", "₹1,337", "$9.99", "$1.99"]) == "$"

    def test_identical_symbols_match(self):
        assert est.currency_matches("$", "$") is True

    def test_symbol_and_its_iso_code_match(self):
        assert est.currency_matches("₹", "INR") is True
        assert est.currency_matches("$", "USD") is True

    def test_different_currencies_do_not_match(self):
        assert est.currency_matches("$", "₹") is False
        assert est.currency_matches("$", "INR") is False

    def test_unknown_observation_is_not_treated_as_a_mismatch(self):
        # no price data is not evidence of the wrong currency
        assert est.currency_matches("$", None) is True

    def test_income_refuses_to_compute_on_mismatched_currency(self):
        books = [{"bsr": 5_000, "price": 1337.0}]
        r = est.niche_income(books, ku_share=0.5, currency_ok=False)
        assert r["books_counted"] == 0
        assert r["total_mid"] == 0.0
        assert r["confidence"] == "none"
        assert "currency" in r["basis"].lower()


class TestMoneyGuard:
    """Live scan on an Indian IP: the headline income was suppressed but the
    money-proof table still showed '$1887.42' and '$2,636/mo' — INR figures
    with a dollar sign. Per-book money must obey the same currency guard."""

    rows = [{"asin": "A", "price": 1887.42, "est_sales_per_day": 0.13, "est_monthly_royalty": 2636.0},
            {"asin": "B", "price": None, "est_sales_per_day": None, "est_monthly_royalty": None}]

    def test_mismatched_currency_blanks_royalty_but_keeps_bsr_sales(self):
        out = est.money_guard(self.rows, currency_ok=False)
        assert out[0]["est_monthly_royalty"] is None
        assert out[0]["est_sales_per_day"] == 0.13   # BSR-derived, currency-free
        assert out[0]["money_suppressed"] is True

    def test_matching_currency_passes_through(self):
        out = est.money_guard(self.rows, currency_ok=True)
        assert out[0]["est_monthly_royalty"] == 2636.0
        assert "money_suppressed" not in out[0]
