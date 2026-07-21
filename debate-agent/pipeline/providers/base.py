"""Provider interface for the two LLM pipeline stages.

Every method raises PipelineError (with a user-safe message) on failure and
returns a usage dict alongside its result so the session's logging fields
(input_tokens / output_tokens, plus provider extras) stay identical across
providers.
"""

from abc import ABC, abstractmethod

from pipeline.models import Verdict

# The interface's verdict payload is the existing Verdict dataclass:
# {verdict, confidence, summary, sources[]} plus the claim itself.
VerdictResult = Verdict


class LLMProvider(ABC):
    name: str = "unknown"
    extraction_model: str = "unknown"
    verification_model: str = "unknown"

    @abstractmethod
    async def extract_claims(self, topic: str, window_text: str) -> "tuple[list[str], dict]":
        """Up to 2 self-contained, objectively checkable claims from one
        speaker's transcript window. Returns (claims, usage)."""

    @abstractmethod
    async def verify_claim(self, topic: str, claim: str) -> "tuple[VerdictResult, dict]":
        """Search-grounded verdict for a single claim. Returns (verdict, usage)."""

    @abstractmethod
    async def complete_json(
        self,
        system: str,
        user: str,
        *,
        image_bytes: "bytes | None" = None,
        image_mime: str = "image/jpeg",
    ) -> "tuple[dict, dict]":
        """Generic strict-JSON completion (system + user + optional image).
        The upcoming moderation screening reuses this. Returns (data, usage)."""

    async def aclose(self) -> None:  # optional cleanup
        return None
