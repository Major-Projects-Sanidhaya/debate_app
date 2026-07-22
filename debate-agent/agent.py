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

from config import (
    DRAIN_TIMEOUT_SECONDS,
    IO_DRAIN_TIMEOUT_SECONDS,
    AgentConfig,
)
from logging_config import configure_logging
from moderation.config import ModerationConfig
from moderation.frames import frame_to_jpeg
from moderation.internal_client import InternalModerationClient
from moderation.moderator import Moderator
from pipeline.cache import ClaimCache
from pipeline.models import parse_room_metadata
from pipeline.providers import get_provider
from session import DebateSession, stance_of
from transcript import RollingTranscript, TranscriptMirror

# livekit-agents owns the stdlib root logger (JSON in `start` mode); adding our
# own handler would print every library record a second time. Our structlog
# logs go straight to stdout as JSON either way.
configure_logging(configure_stdlib=False)
logger = structlog.get_logger("debate-agent")

AGENT_IDENTITY = "fc-agent"
DATA_TOPIC = "fact_check"
STT_SAMPLE_RATE = 16000


async def request_fnc(req: JobRequest) -> None:
    # Identity is contractual: debate-mobile hides this participant by identity.
    await req.accept(identity=AGENT_IDENTITY, name="Fact Checker")


async def entrypoint(ctx: JobContext) -> None:
    mod_config = ModerationConfig.from_env()
    # Video moderation needs the video tracks; otherwise stay audio-only.
    await ctx.connect(
        auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL
        if mod_config.video_enabled
        else AutoSubscribe.AUDIO_ONLY
    )

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

    transcript = RollingTranscript()
    session = DebateSession(
        meta,
        provider=provider,
        cache=ClaimCache(redis_client),
        transcript=transcript,
        mirror=TranscriptMirror(redis_client, meta.match_id),
        publish=publish,
    )

    internal_client = InternalModerationClient(
        mod_config.internal_api_url, mod_config.internal_api_key
    )
    moderator = Moderator(
        match_id=meta.match_id,
        provider=provider,
        internal_client=internal_client,
        transcript=transcript,
        config=mod_config,
    )
    logger.info(
        "moderation_configured",
        video_enabled=mod_config.video_enabled,
        video_sample_interval=mod_config.video_sample_interval,
        test_phrase_set=bool(mod_config.test_phrase),
        internal_api_url=mod_config.internal_api_url,
    )
    if mod_config.test_phrase:
        logger.warning("moderation_test_phrase_active", hint="unset MODERATION_TEST_PHRASE in production")

    # Constructing the STT validates DEEPGRAM_API_KEY; streams start per track.
    stt_impl = deepgram.STT(sample_rate=STT_SAMPLE_RATE)

    # Long-lived per-track loops: they never finish on their own, so shutdown
    # cancels them outright.
    stream_tasks: "list[asyncio.Task]" = []
    # Short-lived deliveries (moderation POSTs, data-channel publishes): these
    # get a bounded window to land before the clients close under them.
    io_tasks: "set[asyncio.Task]" = set()
    transcribing_sids: "set[str]" = set()
    moderating_sids: "set[str]" = set()

    def spawn_io(coro) -> None:
        task = asyncio.create_task(coro)
        io_tasks.add(task)
        task.add_done_callback(io_tasks.discard)

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
                        # Fire-and-forget: moderation never delays fact-checking.
                        spawn_io(moderator.on_final_segment(stance, text))
        finally:
            pump_task.cancel()
            await stt_stream.aclose()
            await audio.aclose()

    async def moderate_video_track(track: rtc.Track, participant: rtc.RemoteParticipant) -> None:
        """Sample one frame per VIDEO_SAMPLE_INTERVAL and screen it in memory.
        Frames are never written to disk, Redis, or logs."""
        stance = stance_of(participant.identity, participant.name, meta)
        if stance is None:
            return
        logger.info("moderating_video", identity=participant.identity, stance=stance)
        video = rtc.VideoStream(track)
        try:
            async for frame_event in video:
                if not moderator.should_sample_video(stance):
                    continue
                try:
                    jpeg = frame_to_jpeg(frame_event.frame)
                except Exception:
                    logger.warning("frame_encode_failed", stance=stance)
                    continue
                await moderator.on_video_frame(stance, jpeg)
                del jpeg  # bytes live only for the classification call
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("video_moderation_failed", stance=stance)
        finally:
            await video.aclose()

    def start_transcriber(track: rtc.Track, participant: rtc.RemoteParticipant) -> None:
        if track.sid in transcribing_sids or participant.identity == AGENT_IDENTITY:
            return
        transcribing_sids.add(track.sid)
        stream_tasks.append(asyncio.create_task(transcribe_track(track, participant)))

    def start_video_moderation(track: rtc.Track, participant: rtc.RemoteParticipant) -> None:
        if not mod_config.video_enabled:
            return
        if track.sid in moderating_sids or participant.identity == AGENT_IDENTITY:
            return
        moderating_sids.add(track.sid)
        stream_tasks.append(asyncio.create_task(moderate_video_track(track, participant)))

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track, publication, participant) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            start_transcriber(track, participant)
        elif track.kind == rtc.TrackKind.KIND_VIDEO:
            start_video_moderation(track, participant)

    @ctx.room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant) -> None:
        # Data messages are not replayed to late joiners, and debaters connect
        # after we do (the room exists before match_found is sent) — so
        # re-announce readiness on every join. Clients treat it idempotently.
        spawn_io(publish({"type": "agent_ready"}))

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
            spawn_io(
                session.handle_fact_check_request(
                    packet.participant.identity, packet.participant.name
                )
            )

    # Tracks published before we connected don't re-fire track_subscribed.
    for participant in ctx.room.remote_participants.values():
        for publication in participant.track_publications.values():
            track = publication.track
            if track is None:
                continue
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                start_transcriber(track, participant)
            elif track.kind == rtc.TrackKind.KIND_VIDEO:
                start_video_moderation(track, participant)

    async def cleanup() -> None:
        """Job shutdown (SIGTERM drain, room close, or a normal end).

        Order matters: the per-track loops are cancelled first because they
        never finish on their own, then in-flight moderation POSTs and
        data-channel publishes get a bounded window to land — otherwise
        closing the HTTP client would cancel a severe moderation event that
        debate-api needs in order to terminate the match.
        """
        for task in stream_tasks:
            task.cancel()

        pending = set(io_tasks)
        if pending:
            _, unfinished = await asyncio.wait(pending, timeout=IO_DRAIN_TIMEOUT_SECONDS)
            if unfinished:
                logger.warning(
                    "shutdown_io_drain_incomplete",
                    pending=len(unfinished),
                    timeout_s=IO_DRAIN_TIMEOUT_SECONDS,
                )
                for task in unfinished:
                    task.cancel()

        await asyncio.gather(*stream_tasks, *io_tasks, return_exceptions=True)
        await internal_client.aclose()
        await redis_client.aclose()
        await provider.aclose()
        logger.info("job_shutdown_complete")

    ctx.add_shutdown_callback(cleanup)

    # STT is constructed and track handlers are live: announce readiness.
    await publish({"type": "agent_ready"})
    logger.info("agent_ready")


if __name__ == "__main__":
    # Fatal before the worker registers: never run production against dev
    # credentials or without the keys the selected provider needs.
    _config = AgentConfig.from_env()
    _config.enforce_production_guards()
    logger.info(
        "worker_starting",
        env=_config.env,
        livekit_url=_config.livekit_url,
        llm_provider=_config.llm_provider,
    )

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
            # On SIGTERM the worker stops accepting jobs and lets in-room
            # debates finish. The library default is 3600s, which would hold a
            # deploy for an hour.
            drain_timeout=DRAIN_TIMEOUT_SECONDS,
        )
    )
