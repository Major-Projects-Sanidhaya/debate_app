"""Moderation configuration from env."""

import os
from dataclasses import dataclass

# Screening cadence is fixed policy, not env-tunable: one transcript screening
# per speaker per 15s, classifying that speaker's last 30s.
TEXT_SCREEN_INTERVAL_SECONDS = 15.0
TEXT_WINDOW_SECONDS = 30.0
MAX_EXCERPT_CHARS = 300


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw and raw.strip() else default
    except ValueError:
        return default


@dataclass
class ModerationConfig:
    internal_api_url: str = "http://localhost:8000"
    internal_api_key: str = ""
    test_phrase: str = ""
    video_sample_interval: float = 10.0
    video_enabled: bool = True
    text_screen_interval: float = TEXT_SCREEN_INTERVAL_SECONDS
    text_window_seconds: float = TEXT_WINDOW_SECONDS

    @classmethod
    def from_env(cls) -> "ModerationConfig":
        return cls(
            internal_api_url=os.getenv("INTERNAL_API_URL", "http://localhost:8000"),
            internal_api_key=os.getenv("INTERNAL_API_KEY", ""),
            test_phrase=(os.getenv("MODERATION_TEST_PHRASE") or "").strip(),
            video_sample_interval=_env_float("VIDEO_SAMPLE_INTERVAL", 10.0),
            video_enabled=_env_bool("VIDEO_MODERATION_ENABLED", True),
        )
