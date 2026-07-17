"""Smoke-test the agent's LiveKit layer without Deepgram/Anthropic keys.

Creates a room with contract metadata, joins as the "pro" participant, waits
for the agent's agent_ready, sends a fact_check_request, and prints what comes
back on the fact_check topic. With no opponent audio the expected reply is a
fact_check_error ("opponent hasn't said anything...") — which proves dispatch,
identity, metadata parsing, and the data-channel round trip end to end.

Run (with `python agent.py dev` running in another terminal):
    python scripts/room_probe.py
"""

import asyncio
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from livekit import api, rtc

ROOM = f"match_{uuid.uuid4()}"
METADATA = json.dumps(
    {
        "match_id": ROOM.removeprefix("match_"),
        "topic_id": 1,
        "topic": "Gun control",
        "fact_check_mode": "on_demand",
        "user_pro": "probe-pro",
        "user_con": "probe-con",
    }
)


def http_url(ws_url: str) -> str:
    return "http" + ws_url.removeprefix("ws") if ws_url.startswith("ws") else ws_url


async def main() -> None:
    url = os.environ["LIVEKIT_URL"]
    key, secret = os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"]

    lkapi = api.LiveKitAPI(url=http_url(url), api_key=key, api_secret=secret)
    await lkapi.room.create_room(
        api.CreateRoomRequest(name=ROOM, empty_timeout=120, metadata=METADATA)
    )
    await lkapi.aclose()
    print(f"created room {ROOM}")

    # Join LATE on purpose: the agent's initial agent_ready broadcast happens
    # before real clients connect, so this exercises the re-announce-on-join
    # path that debate-mobile depends on.
    await asyncio.sleep(3)

    token = (
        api.AccessToken(key, secret)
        .with_identity("probe-pro")
        .with_name("pro")
        .with_grants(api.VideoGrants(room_join=True, room=ROOM))
        .to_jwt()
    )

    room = rtc.Room()
    received: "asyncio.Queue[tuple[str, dict]]" = asyncio.Queue()

    @room.on("data_received")
    def on_data(packet: rtc.DataPacket) -> None:
        if packet.topic == "fact_check":
            # The agent is a hidden participant, so clients can't resolve the
            # sender object — its packets arrive with participant=None.
            sender = packet.participant.identity if packet.participant else "<hidden>"
            received.put_nowait((sender, json.loads(packet.data)))

    await room.connect(url, token)
    print("connected as probe-pro (name=pro); waiting for agent_ready...")

    sender, msg = await asyncio.wait_for(received.get(), timeout=20)
    print(f"  <- from {sender}: {msg}")
    assert sender == "<hidden>", f"agent should be hidden, got sender={sender}"
    assert msg == {"type": "agent_ready"}, msg
    visible = [p.identity for p in room.remote_participants.values()]
    assert "fc-agent" not in visible, f"fc-agent leaked into participant list: {visible}"
    print(f"  visible participants: {visible} (agent correctly hidden)")

    await room.local_participant.publish_data(
        json.dumps({"type": "fact_check_request"}).encode(), reliable=True, topic="fact_check"
    )
    print("  -> sent fact_check_request")

    # agent_ready may arrive more than once (initial broadcast + re-announce
    # on our join, depending on dispatch timing) — skip duplicates like a
    # real client does.
    while True:
        sender, msg = await asyncio.wait_for(received.get(), timeout=20)
        print(f"  <- from {sender}: {msg}")
        if msg["type"] != "agent_ready":
            break
    assert msg["type"] in ("fact_check_status", "fact_check_error"), msg

    await room.disconnect()
    print("\nroom probe OK: hidden agent joined, sent agent_ready, and answered a request")


if __name__ == "__main__":
    asyncio.run(main())
