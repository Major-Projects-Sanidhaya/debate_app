"""Provider selection: LLM_PROVIDER env var, default "gemini".

Imports are lazy so selecting one provider never requires the other
provider's SDK or API key.
"""

import os

from pipeline.providers.base import LLMProvider


def get_provider(name: "str | None" = None) -> LLMProvider:
    resolved = (name or os.getenv("LLM_PROVIDER", "gemini")).strip().lower()
    if resolved == "gemini":
        from pipeline.providers.gemini_provider import GeminiProvider

        return GeminiProvider()
    if resolved == "anthropic":
        from pipeline.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    raise ValueError(f"unknown LLM_PROVIDER {resolved!r} — expected 'gemini' or 'anthropic'")
