"""Worker configuration and production boot guards.

This module only *validates* — the runtime config lives where it is used
(moderation/config.py for moderation, pipeline/providers for the LLM, and
livekit-agents reads LIVEKIT_* from the environment itself). Its job is to
refuse to start a production worker that is wired to development credentials.
"""

import os
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)

DEV_LIVEKIT_KEY = "devkey"
DEV_LIVEKIT_SECRET = "devsecret_change_me"
DEV_INTERNAL_KEYS = frozenset({"", "dev_internal_key_change_me"})

# Which API key each provider needs; also the set of providers we accept.
PROVIDER_KEY_VARS = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}

# How long shutdown waits for in-flight moderation POSTs and data-channel
# publishes before cancelling them. Kept under livekit-agents'
# shutdown_process_timeout (10s) so cleanup finishes before the job process is
# killed.
IO_DRAIN_TIMEOUT_SECONDS = 5.0

# Bounds how long a SIGTERM'd worker keeps serving in-room debates before
# exiting. livekit-agents defaults to 3600s, which would stall a deploy for an
# hour; a debate that has not finished in 5 minutes can be dropped.
DRAIN_TIMEOUT_SECONDS = 300


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


@dataclass
class AgentConfig:
    env: str = "development"
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    deepgram_api_key: str = ""
    llm_provider: str = "gemini"
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    internal_api_key: str = ""

    @classmethod
    def from_env(cls) -> "AgentConfig":
        return cls(
            env=_env("ENV") or "development",
            livekit_url=_env("LIVEKIT_URL"),
            livekit_api_key=_env("LIVEKIT_API_KEY"),
            livekit_api_secret=_env("LIVEKIT_API_SECRET"),
            deepgram_api_key=_env("DEEPGRAM_API_KEY"),
            llm_provider=(_env("LLM_PROVIDER") or "gemini").lower(),
            gemini_api_key=_env("GEMINI_API_KEY"),
            anthropic_api_key=_env("ANTHROPIC_API_KEY"),
            internal_api_key=_env("INTERNAL_API_KEY"),
        )

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    def provider_key(self) -> str:
        return {
            "gemini": self.gemini_api_key,
            "anthropic": self.anthropic_api_key,
        }.get(self.llm_provider, "")

    def production_problems(self) -> "list[str]":
        """Config that must never reach production. Empty list == good to go."""
        problems: list[str] = []

        if self.livekit_api_key == DEV_LIVEKIT_KEY:
            problems.append(
                'LIVEKIT_API_KEY is still the dev server\'s "devkey" — use the '
                "LiveKit Cloud project key"
            )
        if self.livekit_api_secret == DEV_LIVEKIT_SECRET:
            problems.append(
                "LIVEKIT_API_SECRET is still the dev server's default — use the "
                "LiveKit Cloud project secret"
            )
        if not self.livekit_api_key:
            problems.append("LIVEKIT_API_KEY is not set")

        if not self.deepgram_api_key:
            problems.append(
                "DEEPGRAM_API_KEY is not set — the worker cannot transcribe without it"
            )

        if self.internal_api_key in DEV_INTERNAL_KEYS:
            problems.append(
                "INTERNAL_API_KEY is not set — moderation events would be rejected "
                "by debate-api (it must match debate-api's INTERNAL_API_KEY)"
            )

        # The provider that is actually selected is the only one that needs a key.
        key_var = PROVIDER_KEY_VARS.get(self.llm_provider)
        if key_var is None:
            problems.append(
                f"LLM_PROVIDER={self.llm_provider!r} is not one of "
                f"{sorted(PROVIDER_KEY_VARS)}"
            )
        elif not self.provider_key():
            problems.append(
                f"{key_var} is not set, but LLM_PROVIDER={self.llm_provider} "
                "requires it"
            )

        return problems

    def enforce_production_guards(self) -> None:
        """Refuse to boot a production worker with development credentials."""
        if not self.is_production:
            return
        problems = self.production_problems()
        if not problems:
            return
        for problem in problems:
            logger.error("fatal_config_error", problem=problem, env=self.env)
        raise RuntimeError("refusing to start with ENV=production: " + "; ".join(problems))
