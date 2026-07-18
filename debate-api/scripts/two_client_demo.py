from dotenv import load_dotenv
load_dotenv()

"""Acceptance demo: two device users join opposite stances and both get match_found.

Requires the api running (uvicorn app.main:app) and debate-infra up.
Run: python -m scripts.two_client_demo [--base http://localhost:8000]
"""

import argparse
import asyncio
import json
import uuid

import httpx
import jwt
import websockets
from livekit import api as lk_api

from app.config import get_settings
from app.livekit_rooms import make_livekit_api


async def wait_for(ws, wanted_type: str) -> dict:
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        print(f"    <- {msg['type']}")
        if msg["type"] == wanted_type:
            return msg
        if msg["type"] == "error":
            raise RuntimeError(f"server error: {msg['message']}")


async def run_client(name: str, base: str, ws_base: str, topic_id: int, stance: str, mode: str):
    async with httpx.AsyncClient(base_url=base) as http:
        r = await http.post("/auth/device", json={"device_id": str(uuid.uuid4()), "over_18": True})
        r.raise_for_status()
        auth = r.json()
    print(f"[{name}] authed as user {auth['user_id']}")

    async with websockets.connect(f"{ws_base}/ws/match?token={auth['token']}") as ws:
        await ws.send(json.dumps(
            {"type": "join", "topic_id": topic_id, "stance": stance, "fact_check_mode": mode}
        ))
        print(f"[{name}] joined topic={topic_id} stance={stance} mode={mode}")
        match = await wait_for(ws, "match_found")

    claims = jwt.decode(match["livekit_token"], options={"verify_signature": False})
    print(
        f"[{name}] MATCH {match['match_id']}\n"
        f"    room={match['room_name']} topic={match['topic']['title']!r}\n"
        f"    your_stance={match['your_stance']} peer_stance={match['peer_stance']} "
        f"fact_check_mode={match['fact_check_mode']}\n"
        f"    livekit_url={match['livekit_url']}\n"
        f"    lk token: identity={claims['sub']} room={claims['video']['room']} "
        f"join={claims['video']['roomJoin']}"
    )
    return auth, match


async def verify_room_metadata(match: dict, auth_pro: dict, auth_con: dict) -> None:
    """Fetch the LiveKit room via RoomService and check the metadata contract
    that debate-agent will rely on."""
    lkapi = make_livekit_api(get_settings())
    try:
        resp = await lkapi.room.list_rooms(lk_api.ListRoomsRequest(names=[match["room_name"]]))
    finally:
        await lkapi.aclose()
    assert resp.rooms, f"room {match['room_name']} not found on the LiveKit server"
    room = resp.rooms[0]
    meta = json.loads(room.metadata)
    print(f"\nroom {room.name} metadata (as debate-agent will read it):")
    print(json.dumps(meta, indent=2))

    assert meta["match_id"] == match["match_id"]
    assert meta["topic_id"] == match["topic"]["id"]
    assert meta["topic"] == match["topic"]["title"]
    assert meta["fact_check_mode"] == match["fact_check_mode"]
    assert meta["user_pro"] == auth_pro["user_id"]
    assert meta["user_con"] == auth_con["user_id"]
    assert room.empty_timeout == 300, f"empty_timeout={room.empty_timeout}"
    print("room metadata OK")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8000")
    args = parser.parse_args()
    ws_base = args.base.replace("http://", "ws://").replace("https://", "wss://")

    async with httpx.AsyncClient(base_url=args.base) as http:
        r = await http.post("/auth/device", json={"device_id": str(uuid.uuid4()), "over_18": True})
        r.raise_for_status()
        probe = r.json()
        topics = (await http.get(
            "/topics", headers={"Authorization": f"Bearer {probe['token']}"}
        )).json()
    topic = topics[0]
    print(f"using topic {topic['id']}: {topic['title']!r}\n")

    (auth_a, match_a), (auth_b, match_b) = await asyncio.gather(
        run_client("pro-client", args.base, ws_base, topic["id"], "pro", "auto"),
        run_client("con-client", args.base, ws_base, topic["id"], "con", "on_demand"),
    )

    assert match_a["match_id"] == match_b["match_id"], "clients got different matches!"
    assert {match_a["your_stance"], match_b["your_stance"]} == {"pro", "con"}
    assert match_a["fact_check_mode"] == "on_demand", "auto+on_demand must resolve to on_demand"

    await verify_room_metadata(match_a, auth_a, auth_b)

    async with httpx.AsyncClient(base_url=args.base) as http:
        r = await http.post(
            f"/matches/{match_a['match_id']}/end",
            headers={"Authorization": f"Bearer {auth_a['token']}"},
        )
        assert r.status_code == 204, r.text
    print("\nmatch ended via POST /matches/{id}/end -> 204")
    print("demo OK: both clients matched into the same room with valid LiveKit tokens")


if __name__ == "__main__":
    asyncio.run(main())
