"""Tests for LLM provider selection in the Niche Brief.

`brief.py` used to snapshot GEMINI_API_KEY into a module constant at import
time. Because `app.py` imports brief at startup, a key placed in `.env` (or
exported after the module loaded) was silently ignored and the brief just
said "narrative off" — with no hint that the key had been seen at all.
Reading the environment at call time removes that whole failure mode.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import brief


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_MODEL", "ANTHROPIC_MODEL"):
        monkeypatch.delenv(var, raising=False)


class TestProviderSelection:
    def test_no_keys_means_no_provider(self):
        assert brief.active_provider() is None

    def test_gemini_key_set_after_import_is_still_picked_up(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        provider = brief.active_provider()
        assert provider is not None
        assert provider["name"] == "gemini"

    def test_gemini_model_defaults_when_unset(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        assert brief.active_provider()["model"] == "gemini-2.5-flash"

    def test_gemini_model_is_overridable(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("GEMINI_MODEL", "gemini-3-pro")
        assert brief.active_provider()["model"] == "gemini-3-pro"

    def test_anthropic_is_used_when_only_it_is_set(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        assert brief.active_provider()["name"] == "anthropic"

    def test_gemini_wins_when_both_are_set(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "g")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
        assert brief.active_provider()["name"] == "gemini"

    def test_blank_key_is_not_a_provider(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "   ")
        assert brief.active_provider() is None


class TestNarrativeSourceReporting:
    def test_says_which_provider_ran(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(brief, "_call_gemini", lambda digest: "narrative text")
        out = brief.enhance_with_llm(_stub_brief())
        assert out["narrative"] == "narrative text"
        assert out["narrative_source"].startswith("gemini:")

    def test_reports_the_gap_when_no_key_is_configured(self):
        out = brief.enhance_with_llm(_stub_brief())
        assert "narrative" not in out
        assert "GEMINI_API_KEY" in out["narrative_source"]

    def test_a_failing_call_degrades_instead_of_raising(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        def boom(digest):
            raise RuntimeError("quota exceeded")

        monkeypatch.setattr(brief, "_call_gemini", boom)
        out = brief.enhance_with_llm(_stub_brief())
        assert "quota exceeded" in out["narrative_source"]
        # the deterministic layer must survive an LLM outage
        assert out["verdict"] == "GO"


def _stub_brief() -> dict:
    return {"seed": "adhd", "focus_keyword": "adhd for beginners", "verdict": "GO",
            "gates": [], "breakeven": {}, "ku_read": {}, "evidence": {},
            "differentiation": [], "warnings": []}


class TestGenericLlmCall:
    """Discovery needs the model for its own prompt, not the brief's.

    `_call_gemini` prepends the Niche Brief analyst prompt to whatever it is
    given, so reusing it for concept extraction produced a narrative instead
    of JSON. A generic caller sends exactly the prompt it is handed.
    """

    def test_generic_caller_sends_the_prompt_verbatim(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        seen = {}

        def fake_post(prompt, provider, max_output_tokens=3000):
            seen["prompt"] = prompt
            return "ok"

        monkeypatch.setattr(brief, "_post_prompt", fake_post)
        brief.call_llm("RETURN ONLY JSON")
        assert seen["prompt"] == "RETURN ONLY JSON"
        assert "analyst" not in seen["prompt"].lower()

    def test_generic_caller_returns_none_without_a_provider(self):
        assert brief.call_llm("anything") is None

    def test_the_brief_still_gets_its_own_prompt_prepended(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        seen = {}

        def fake_post(prompt, provider, max_output_tokens=3000):
            seen["prompt"] = prompt
            return "narrative"

        monkeypatch.setattr(brief, "_post_prompt", fake_post)
        brief._call_gemini("DIGEST")
        assert seen["prompt"].endswith("DIGEST")
        assert len(seen["prompt"]) > len("DIGEST"), "the brief prompt must still be prepended"


class TestOutputBudget:
    """Structured extraction needs a bigger cap than the brief's narrative."""

    def test_caller_accepts_a_larger_output_budget(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        seen = {}

        def fake_post(prompt, provider, max_output_tokens=3000):
            seen["cap"] = max_output_tokens
            return "ok"

        monkeypatch.setattr(brief, "_post_prompt", fake_post)
        brief.call_llm("p", max_output_tokens=8192)
        assert seen["cap"] == 8192

    def test_default_budget_is_unchanged_for_existing_callers(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        seen = {}

        def fake_post(prompt, provider, max_output_tokens=3000):
            seen["cap"] = max_output_tokens
            return "ok"

        monkeypatch.setattr(brief, "_post_prompt", fake_post)
        brief.call_llm("p")
        assert seen["cap"] == 3000
