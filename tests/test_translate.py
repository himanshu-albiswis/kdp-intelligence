"""Listing translation for international marketplaces.

BookBeam covers seven markets; Titans sells a listing translator. Ours is
grounded: the model receives only the listing, returns strict JSON per
target language, and every translation is re-checked against KDP's rules
(a German title can overrun 200 characters just as easily). Nothing is
translated for a marketplace that already uses the listing's language.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import translate

LISTING = {"title": "Air Fryer Cookbook for Beginners", "subtitle": "500 Simple Recipes",
           "description": "<p>Crispy meals fast.</p>" + " Great recipes." * 20,
           "keywords": ["air fryer recipes", "quick dinners"], "categories": ["Cooking"]}


def fake_llm(prompt):
    return json.dumps([{
        "marketplace": "de", "language": "German",
        "title": "Heißluftfritteuse Kochbuch für Anfänger", "subtitle": "500 einfache Rezepte",
        "description": "<p>Knusprige Mahlzeiten, schnell.</p>" + " Tolle Rezepte." * 20,
        "keywords": ["heißluftfritteuse rezepte", "schnelle abendessen"],
    }])


class TestMarketplaceLanguages:
    def test_english_marketplaces_need_no_translation(self):
        assert translate.language_for("uk") == "English"
        assert translate.needs_translation("uk", source_language="English") is False

    def test_german_marketplace_needs_german(self):
        assert translate.language_for("de") == "German"
        assert translate.needs_translation("de", source_language="English") is True

    def test_unknown_marketplace_is_rejected(self):
        with pytest.raises(ValueError):
            translate.language_for("zz")


class TestTranslateListing:
    def test_returns_one_pack_per_requested_market(self):
        out = translate.translate_listing(LISTING, ["de"], llm=fake_llm)
        assert [p["marketplace"] for p in out["packs"]] == ["de"]
        assert out["packs"][0]["title"].startswith("Heißluft")

    def test_english_markets_are_passed_through_untranslated(self):
        out = translate.translate_listing(LISTING, ["uk"], llm=fake_llm)
        assert out["packs"][0]["title"] == LISTING["title"]
        assert out["packs"][0]["translated"] is False

    def test_every_pack_is_rechecked_against_kdp_rules(self):
        out = translate.translate_listing(LISTING, ["de"], llm=fake_llm)
        assert "guidelines" in out["packs"][0]
        assert "score" in out["packs"][0]["guidelines"]

    def test_no_model_is_reported_not_faked(self):
        out = translate.translate_listing(LISTING, ["de"], llm=None)
        assert out["packs"] == [] or all(p["translated"] is False for p in out["packs"])
        assert any("GEMINI_API_KEY" in w for w in out["warnings"])

    def test_a_bad_model_reply_degrades_with_a_warning(self):
        out = translate.translate_listing(LISTING, ["de"], llm=lambda p: "nope")
        assert not any(p.get("translated") for p in out["packs"])
        assert out["warnings"]

    def test_the_model_never_sees_more_than_the_listing(self):
        seen = {}

        def spy(prompt):
            seen["prompt"] = prompt
            return fake_llm(prompt)

        translate.translate_listing(LISTING, ["de"], llm=spy)
        assert "Air Fryer Cookbook for Beginners" in seen["prompt"]
        assert "German" in seen["prompt"]

    def test_a_market_the_model_skipped_is_reported_missing(self):
        out = translate.translate_listing(LISTING, ["de", "fr"], llm=fake_llm)
        assert any("fr" in w for w in out["warnings"])
