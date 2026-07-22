"""Endpoint resolution shared by the demo / smoke-test scripts.

The same scripts run against local dev and against production:

    python -m scripts.two_client_demo
    API_BASE=https://debate-api.up.railway.app python -m scripts.two_client_demo

`load_dotenv()` is deliberately called WITHOUT override=True, so real
environment variables win over whatever is in .env — that is what lets a
production run reuse this repo's .env for everything except the vars it
explicitly overrides.
"""

import os

from dotenv import load_dotenv

load_dotenv()

DEFAULT_API_BASE = "http://localhost:8000"


def api_base() -> str:
    """Base HTTP(S) URL of the API, from API_BASE (default localhost)."""
    return os.getenv("API_BASE", DEFAULT_API_BASE).rstrip("/")


def ws_base(base: str | None = None) -> str:
    """Websocket origin derived from the API base's scheme (https -> wss)."""
    resolved = (base or api_base()).rstrip("/")
    if resolved.startswith("https://"):
        return "wss://" + resolved[len("https://") :]
    if resolved.startswith("http://"):
        return "ws://" + resolved[len("http://") :]
    return resolved
