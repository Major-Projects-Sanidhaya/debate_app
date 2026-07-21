"""Validate keys and prompts: extraction -> verification on a mock transcript.

Runs against the ACTIVE provider (LLM_PROVIDER env: gemini default, or
anthropic) with no LiveKit, Deepgram, or Redis. Run this first:

    python scripts/pipeline_demo.py
"""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from logging_config import configure_logging
from pipeline.providers import get_provider

TOPIC = "Gun control"

KEY_VARS = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}

# ~20 lines of plausible debate audio for one speaker window each.
MOCK_TRANSCRIPT = [
    ("pro", "Look, we have to start with the basic facts here."),
    ("pro", "The United States has more guns than people, over 390 million civilian firearms."),
    ("pro", "And gun deaths in the US hit about 48,000 in 2022, that's a record."),
    ("pro", "No other wealthy country comes close to that number."),
    ("pro", "I just think that's morally unacceptable for a developed nation."),
    ("con", "Hold on, most of those deaths you're citing are suicides, not homicides."),
    ("con", "More than half of US gun deaths are suicides, so the framing is misleading."),
    ("con", "And violent crime has actually fallen dramatically since the early nineties."),
    ("con", "The FBI's own data shows violent crime is way down from its 1991 peak."),
    ("con", "Meanwhile Americans use guns defensively hundreds of thousands of times a year."),
    ("pro", "Those defensive gun use numbers are wildly contested and you know it."),
    ("pro", "States with universal background checks have lower gun death rates."),
    ("pro", "Connecticut's permit law cut gun homicides by 40 percent, that's a real study."),
    ("con", "And Chicago has some of the strictest gun laws and still has high shootings."),
    ("con", "Criminals don't follow background checks, that's just common sense."),
    ("con", "The Second Amendment is an individual right, Heller settled that in 2008."),
    ("pro", "Heller also said the right isn't unlimited, Scalia wrote that himself."),
    ("pro", "I believe reasonable regulation is compatible with the Second Amendment."),
    ("con", "Your side always says reasonable but it always means more restrictions."),
    ("con", "Australia-style confiscation would never work in a country this armed."),
]


def window_for(stance: str) -> str:
    return " ".join(text for s, text in MOCK_TRANSCRIPT if s == stance)


async def check_speaker(provider, stance: str) -> None:
    window = window_for(stance)
    print(f"\n{'=' * 72}\nSpeaker: {stance.upper()}  (window: {len(window)} chars)\n{'=' * 72}")

    t0 = time.time()
    claims, usage = await provider.extract_claims(TOPIC, window)
    print(f"\nExtracted {len(claims)} claim(s) in {time.time() - t0:.1f}s  (usage: {usage})")
    for claim in claims:
        print(f"  - {claim}")

    for claim in claims:
        print(f"\n--- verifying: {claim!r}")
        t0 = time.time()
        verdict, vusage = await provider.verify_claim(TOPIC, claim)
        payload = verdict.core_dict()
        payload["latency_s"] = round(time.time() - t0, 1)
        payload["usage"] = vusage
        print(json.dumps(payload, indent=2))


async def main() -> None:
    provider_name = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    key_var = KEY_VARS.get(provider_name)
    if key_var and not os.getenv(key_var):
        sys.exit(
            f"{key_var} is not set for LLM_PROVIDER={provider_name} — "
            "copy .env.example to .env and fill it in."
        )
    configure_logging()

    provider = get_provider()
    print(
        f"provider={provider.name}  "
        f"(extraction={provider.extraction_model}, verification={provider.verification_model})"
    )
    try:
        await check_speaker(provider, "pro")
        await check_speaker(provider, "con")
    finally:
        await provider.aclose()
    print("\npipeline demo OK — prompts and keys are working")


if __name__ == "__main__":
    asyncio.run(main())
