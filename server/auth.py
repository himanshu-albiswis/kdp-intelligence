"""Clerk authentication for the API, with honest fallbacks.

Three modes, decided by what is configured — read at call time, so a key
added to .env later works without a restart (the same trap brief.py fixed):

  clerk   CLERK_SECRET_KEY set. Every /api route except /api/health and
          /api/config requires a valid Clerk session token. Verification is
          Clerk's own: `authenticate_request` checks the Bearer JWT against
          the instance's JWKS, networklessly after the first fetch.
  apikey  only the legacy KDP_API_KEY is set. Behaviour unchanged: mutations
          guarded by the X-API-Key header, reads open.
  open    nothing configured. Local development stays frictionless.

Rules carried over from Clerk's own skill: the secret key never reaches the
client — /api/config exposes only the publishable key — and when keys are
missing we ask the operator for them rather than inventing anything.
"""

import os
from typing import Any, Optional

from clerk_backend_api.security import authenticate_request  # noqa: F401 (patched in tests)
from clerk_backend_api.security.types import AuthenticateRequestOptions

# Paths that must work before anyone is signed in: liveness probes, and the
# config call the sign-in screen itself needs to boot.
OPEN_PATHS = {"/api/health", "/api/config"}


def secret_key() -> str:
    return os.environ.get("CLERK_SECRET_KEY", "").strip()


def publishable_key() -> str:
    return os.environ.get("CLERK_PUBLISHABLE_KEY", "").strip()


def mode() -> str:
    if secret_key():
        return "clerk"
    if os.environ.get("KDP_API_KEY", "").strip():
        return "apikey"
    return "open"


def config_payload(legacy_key_set: bool) -> dict[str, Any]:
    """What the browser may know. Never the secret key."""
    current = mode() if mode() != "apikey" or legacy_key_set else "open"
    payload: dict[str, Any] = {"auth_mode": current}
    if current == "clerk":
        payload["clerk_publishable_key"] = publishable_key()
        if not publishable_key():
            payload["warning"] = ("CLERK_SECRET_KEY is set but CLERK_PUBLISHABLE_KEY "
                                  "is not; the sign-in screen cannot boot without it.")
    return payload


def check_request(request: Any) -> Optional[dict[str, Any]]:
    """Authenticate one request in clerk mode.

    Returns the session payload (sub, session id, …) when signed in and
    raises AuthRejected — carrying a human-readable reason — when not.
    """
    try:
        state = authenticate_request(
            request,
            AuthenticateRequestOptions(secret_key=secret_key()),
        )
    except Exception as exc:  # noqa: BLE001 - a broken verifier must read as
        # "not signed in", never as a server error that looks like our bug.
        raise AuthRejected(f"could not verify the session ({type(exc).__name__}: {exc}); "
                           f"please sign in again") from exc

    if not getattr(state, "is_signed_in", False):
        message = getattr(state, "message", None) or getattr(state, "reason", None)
        detail = "Please sign in to use the API"
        if message:
            detail = f"{detail} ({message})"
        raise AuthRejected(detail)

    return getattr(state, "payload", None) or {}


class AuthRejected(Exception):
    """A 401 with a human-readable reason."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail
