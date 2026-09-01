"""Tests for Clerk authentication.

Three modes, decided by environment at call time (a key added to .env after
startup must work — the same lesson brief.py taught):

  clerk   CLERK_SECRET_KEY set — every /api route except health and config
          requires a valid Clerk session token
  apikey  legacy KDP_API_KEY only — mutations guarded, reads open (unchanged)
  open    nothing set — local development stays frictionless

The Clerk skill's hard rules hold: the secret key never reaches the client
(config exposes only the publishable key), and missing keys are requested
from the user, never invented.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))

from fastapi.testclient import TestClient

import app as app_mod

# The app imports auth flat (--app-dir server); importing server.auth here
# would create a second module object and monkeypatches would land on the
# wrong one. Test the instance the app actually uses.
auth = app_mod.auth_mod


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("CLERK_SECRET_KEY", "CLERK_PUBLISHABLE_KEY", "KDP_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(app_mod, "API_KEY", "", raising=False)


@pytest.fixture
def client():
    return TestClient(app_mod.app, raise_server_exceptions=False)


class TestModeSelection:
    def test_nothing_configured_is_open(self):
        assert auth.mode() == "open"

    def test_clerk_secret_selects_clerk(self, monkeypatch):
        monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_x")
        assert auth.mode() == "clerk"

    def test_clerk_wins_over_the_legacy_api_key(self, monkeypatch):
        monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_x")
        monkeypatch.setenv("KDP_API_KEY", "k")
        assert auth.mode() == "clerk"

    def test_keys_are_read_at_call_time_not_import_time(self, monkeypatch):
        assert auth.mode() == "open"
        monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_x")
        assert auth.mode() == "clerk"


class TestConfigEndpoint:
    def test_open_mode_says_so(self, client):
        data = client.get("/api/config").json()
        assert data["auth_mode"] == "open"

    def test_clerk_mode_exposes_only_the_publishable_key(self, client, monkeypatch):
        monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_SECRET")
        monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", "pk_test_PUBLIC")
        data = client.get("/api/config").json()
        assert data["auth_mode"] == "clerk"
        assert data["clerk_publishable_key"] == "pk_test_PUBLIC"
        assert "sk_test_SECRET" not in str(data), "the secret key must never leave the server"


class TestClerkGate:
    def test_api_routes_refuse_requests_without_a_session(self, client, monkeypatch):
        monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_x")
        response = client.get("/api/research")
        assert response.status_code == 401
        assert "sign in" in response.json()["detail"].lower()

    def test_health_and_config_stay_open_for_probes_and_boot(self, client, monkeypatch):
        monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_x")
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/config").status_code == 200

    def test_the_page_itself_still_loads_so_sign_in_can_render(self, client, monkeypatch):
        monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_x")
        assert client.get("/").status_code == 200

    def test_a_valid_session_passes_through(self, client, monkeypatch):
        monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_x")

        class SignedIn:
            is_signed_in = True
            payload = {"sub": "user_123"}
            reason = None
            message = None

        monkeypatch.setattr(auth, "authenticate_request", lambda req, opts: SignedIn())
        response = client.get("/api/research",
                              headers={"Authorization": "Bearer session-token"})
        assert response.status_code == 200

    def test_a_rejected_token_returns_401_with_clerks_reason(self, client, monkeypatch):
        monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_x")

        class SignedOut:
            is_signed_in = False
            payload = None
            reason = None
            message = "token expired"

        monkeypatch.setattr(auth, "authenticate_request", lambda req, opts: SignedOut())
        response = client.get("/api/research",
                              headers={"Authorization": "Bearer stale"})
        assert response.status_code == 401
        assert "expired" in response.json()["detail"]

    def test_verification_errors_read_as_401_not_500(self, client, monkeypatch):
        monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_x")

        def boom(req, opts):
            raise RuntimeError("jwks fetch failed")

        monkeypatch.setattr(auth, "authenticate_request", boom)
        response = client.get("/api/research", headers={"Authorization": "Bearer t"})
        assert response.status_code == 401


class TestLegacyApiKeyModeUnchanged:
    def test_reads_stay_open(self, client, monkeypatch):
        monkeypatch.setattr(app_mod, "API_KEY", "k")
        assert client.get("/api/research").status_code == 200

    def test_mutations_still_require_the_key(self, client, monkeypatch):
        monkeypatch.setattr(app_mod, "API_KEY", "k")
        response = client.post("/api/research", json={"seed": "adhd", "demo": True})
        assert response.status_code == 401

    def test_open_mode_accepts_everything_as_before(self, client):
        assert client.get("/api/research").status_code == 200
