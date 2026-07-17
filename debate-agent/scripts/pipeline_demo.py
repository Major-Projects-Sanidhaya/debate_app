"""Validate keys and prompts: extraction -> verification on a mock transcript.

Uses the real Anthropic API (claim extraction + web-search verification) with
no LiveKit, Deepgram, or Redis. Run this first:

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

from anthropic import AsyncAnthropic

from logging_config import configure_logging
from pipeline.extraction import extract_claims
from pipeline.verification import verify_claim

TOPIC = "Gun control"

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


async def check_speaker(client: AsyncAnthropic, stance: str) -> None:
    window = window_for(stance)
    print(f"\n{'=' * 72}\nSpeaker: {stance.upper()}  (window: {len(window)} chars)\n{'=' * 72}")

    t0 = time.time()
    claims, usage = await extract_claims(client, TOPIC, window)
    print(f"\nExtracted {len(claims)} claim(s) in {time.time() - t0:.1f}s  (usage: {usage})")
    for claim in claims:
        print(f"  - {claim}")

    for claim in claims:
        print(f"\n--- verifying: {claim!r}")
        t0 = time.time()
        verdict, vusage = await verify_claim(client, TOPIC, claim)
        payload = verdict.core_dict()
        payload["latency_s"] = round(time.time() - t0, 1)
        payload["usage"] = vusage
        print(json.dumps(payload, indent=2))


async def main() -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set — copy .env.example to .env and fill it in.")
    configure_logging()

    client = AsyncAnthropic()
    try:
        await check_speaker(client, "pro")
        await check_speaker(client, "con")
    finally:
        await client.close()
    print("\npipeline demo OK — prompts and keys are working")


if __name__ == "__main__":
    asyncio.run(main())
