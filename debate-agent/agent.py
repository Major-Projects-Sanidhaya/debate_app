"""debate-agent: LiveKit Agents worker that fact-checks live debates.

Joins every new room as hidden participant "fc-agent" (debate-mobile filters
this identity out of the UI), transcribes both debaters with Deepgram, and
publishes verdicts on the "fact_check" data topic. Run: python agent.py dev
"""

import asyncio
import json
import os

from dotenv import load_dotenv

load_dotenv()

import redis.asyncio as aioredis
import structlog
from livekit import rtc
from livekit.agents import (
    AutoSubscribe,
    JobContext,
    JobRequest,
    WorkerOptions,
    WorkerPermissions,
    cli,
)
from livekit.agents import stt as agents_stt
from livekit.plugins import deepgram

from logging_config import configure_logging
from pipeline.cache import ClaimCache
from pipeline.models import parse_room_metadata
from pipeline.providers import get_provider
from session import DebateSession, stance_of
from transcript import RollingTranscript, TranscriptMirror

configure_logging()
logger = structlog.get_logger("debate-agent")

AGENT_IDENTITY = "fc-agent"
DATA_TOPIC = "fact_check"
STT_SAMPLE_RATE = 16000


async def request_fnc(req: JobRequest) -> None:
    # Identity is contractual: debate-mobile hides this participant by identity.
    await req.accept(identity=AGENT_IDENTITY, name="Fact Checker")


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    meta = parse_room_metadata(ctx.room.metadata, room_name=ctx.room.name)
    structlog.contextvars.bind_contextvars(room=ctx.room.name, match_id=meta.match_id)
    logger.info(
        "joined_room",
        topic=meta.topic,
        fact_check_mode=meta.fact_check_mode,
        user_pro=meta.user_pro,
        user_con=meta.user_con,
    )

    redis_client = aioredis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True
    )
    provider = get_provider()  # LLM_PROVIDER env: gemini (default) | anthropic
    logger.info(
        "llm_provider_selected",
        provider=provider.name,
        extraction_model=provider.extraction_model,
        verification_model=provider.verification_model,
    )

    async def publish(payload: dict) -> None:
        await ctx.room.local_participant.publish_data(
            json.dumps(payload).encode("utf-8"), reliable=True, topic=DATA_TOPIC
        )

    session = DebateSession(
        meta,
        provider=provider,
        cache=ClaimCache(redis_client),
        transcript=RollingTranscript(),
        mirror=TranscriptMirror(redis_client, meta.match_id),
        publish=publish,
    )

    # Constructing the STT validates DEEPGRAM_API_KEY; streams start per track.
    stt_impl = deepgram.STT(sample_rate=STT_SAMPLE_RATE)

    tasks: "list[asyncio.Task]" = []
    transcribing_sids: "set[str]" = set()

    async def transcribe_track(track: rtc.Track, participant: rtc.RemoteParticipant) -> None:
        stance = stance_of(participant.identity, participant.name, meta)
        if stance is None:
            logger.warning(
                "unattributable_participant",
                identity=participant.identity,
                name=participant.name,
            )
            return
        logger.info("transcribing_track", identity=participant.identity, stance=stance)
        stt_stream = stt_impl.stream()
        audio = rtc.AudioStream(track, sample_rate=STT_SAMPLE_RATE, num_channels=1)

        async def pump() -> None:
            async for frame_event in audio:
                stt_stream.push_frame(frame_event.frame)
            stt_stream.end_input()

        pump_task = asyncio.create_task(pump())
        try:
            async for event in stt_stream:
                if event.type == agents_stt.SpeechEventType.FINAL_TRANSCRIPT:
                    text = event.alternatives[0].text.strip() if event.alternatives else ""
                    if text:
                        logger.info("final_segment", stance=stance, chars=len(text))
                        await session.on_final_segment(stance, text)
        finally:
            pump_task.cancel()
            await stt_stream.aclose()
            await audio.aclose()

    def start_transcriber(track: rtc.Track, participant: rtc.RemoteParticipant) -> None:
        if track.sid in transcribing_sids or participant.identity == AGENT_IDENTITY:
            return
        transcribing_sids.add(track.sid)
        tasks.append(asyncio.create_task(transcribe_track(track, participant)))

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track, publication, participant) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            start_transcriber(track, participant)

    @ctx.room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant) -> None:
        # Data messages are not replayed to late joiners, and debaters connect
        # after we do (the room exists before match_found is sent) — so
        # re-announce readiness on every join. Clients treat it idempotently.
        tasks.append(asyncio.create_task(publish({"type": "agent_ready"})))

    @ctx.room.on("data_received")
    def on_data_received(packet: rtc.DataPacket) -> None:
        if packet.topic != DATA_TOPIC or packet.participant is None:
            return
        try:
            message = json.loads(packet.data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if isinstance(message, dict) and message.get("type") == "fact_check_request":
            logger.info("fact_check_request", requester=packet.participant.identity)
            tasks.append(
                asyncio.create_task(
                    session.handle_fact_check_request(
                        packet.participant.identity, packet.participant.name
                    )
                )
            )

    # Tracks published before we connected don't re-fire track_subscribed.
    for participant in ctx.room.remote_participants.values():
        for publication in participant.track_publications.values():
            track = publication.track
            if track is not None and track.kind == rtc.TrackKind.KIND_AUDIO:
                start_transcriber(track, participant)

    async def cleanup() -> None:
        for task in tasks:
            task.cancel()
        await redis_client.aclose()
        await provider.aclose()

    ctx.add_shutdown_callback(cleanup)

    # STT is constructed and track handlers are live: announce readiness.
    await publish({"type": "agent_ready"})
    logger.info("agent_ready")


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            request_fnc=request_fnc,
            # Hidden listener: subscribes and speaks on the data channel only.
            permissions=WorkerPermissions(
                can_publish=False,
                can_subscribe=True,
                can_publish_data=True,
                hidden=True,
            ),
        )
    )
