"""Block-exclusion acceptance demo.

Narrative: A(pro) and B(con) match; A blocks B; both re-queue the same topic
and must BOTH stay queued (the Lua exclusion skips the blocked pair, both
directions); then C(con) joins and matches A while B keeps waiting.

Requires the api running (uvicorn app.main:app) and debate-infra up.
Run: python -m scripts.block_demo [--base http://localhost:8000]
"""

import argparse
import asyncio
import json
import uuid

import httpx
import websockets


async def auth(base: str, name: str) -> dict:
    async with httpx.AsyncClient(base_url=base) as http:
        r = await http.post("/auth/device", json={"device_id": str(uuid.uuid4()), "over_18": True})
        r.raise_for_status()
        data = r.json()
    print(f"[{name}] user {data['user_id']}")
    return data


async def ws_connect(ws_base: str, auth_data: dict):
    return await websockets.connect(f"{ws_base}/ws/match?token={auth_data['token']}")


async def join(ws, topic_id: int, stance: str) -> None:
    await ws.send(
        json.dumps(
            {"type": "join", "topic_id": topic_id, "stance": stance, "fact_check_mode": "on_demand"}
        )
    )


async def recv_until(ws, wanted: str, timeout: float = 10) -> dict:
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        if msg["type"] == wanted:
            return msg
        if msg["type"] == "error":
            raise RuntimeError(f"server error: {msg['message']}")


async def assert_no_match(ws, name: str, seconds: float) -> None:
    try:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=seconds))
        if msg.get("type") == "match_found":
            raise AssertionError(f"[{name}] unexpectedly matched: {msg['match_id']}")
        raise AssertionError(f"[{name}] unexpected message: {msg}")
    except TimeoutError:
        print(f"[{name}] still queued after {seconds:.0f}s ✓")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8000")
    args = parser.parse_args()
    base = args.base
    ws_base = base.replace("http://", "ws://").replace("https://", "wss://")

    auth_a = await auth(base, "A")
    auth_b = await auth(base, "B")
    auth_c = await auth(base, "C")

    async with httpx.AsyncClient(base_url=base) as http:
        topics = (
            await http.get("/topics", headers={"Authorization": f"Bearer {auth_a['token']}"})
        ).json()
    topic_id = topics[0]["id"]
    print(f"\ntopic {topic_id}: {topics[0]['title']!r}")

    # --- Step 1: A(pro) + B(con) match -----------------------------------
    print("\nstep 1: A(pro) + B(con) match")
    ws_a = await ws_connect(ws_base, auth_a)
    ws_b = await ws_connect(ws_base, auth_b)
    await join(ws_a, topic_id, "pro")
    await recv_until(ws_a, "queued")
    await join(ws_b, topic_id, "con")
    match_a = await recv_until(ws_a, "match_found")
    await recv_until(ws_b, "match_found")
    print(f"  matched: {match_a['match_id']}")
    await ws_a.close()
    await ws_b.close()

    # --- Step 2: A blocks B ----------------------------------------------
    print("\nstep 2: A blocks B via POST /matches/{id}/block")
    async with httpx.AsyncClient(base_url=base) as http:
        r = await http.post(
            f"/matches/{match_a['match_id']}/block",
            headers={"Authorization": f"Bearer {auth_a['token']}"},
        )
        assert r.status_code == 204, r.text
    print("  204 — B is blocked")

    # --- Step 3: both re-queue; neither may match ------------------------
    print("\nstep 3: both re-queue the same topic — must BOTH stay queued")
    ws_a = await ws_connect(ws_base, auth_a)
    ws_b = await ws_connect(ws_base, auth_b)
    await join(ws_a, topic_id, "pro")
    await recv_until(ws_a, "queued")
    await join(ws_b, topic_id, "con")
    await recv_until(ws_b, "queued")
    await asyncio.gather(
        assert_no_match(ws_a, "A", 3), assert_no_match(ws_b, "B", 3)
    )
    print("  proof: blocked pair was skipped by matchmaking in both directions")

    # --- Step 4: C(con) joins and matches A ------------------------------
    print("\nstep 4: C(con) joins — must match A (B keeps waiting)")
    ws_c = await ws_connect(ws_base, auth_c)
    await join(ws_c, topic_id, "con")
    match_c = await recv_until(ws_c, "match_found")
    match_a2 = await recv_until(ws_a, "match_found")
    assert match_c["match_id"] == match_a2["match_id"], "C and A got different matches"
    print(f"  matched: {match_c['match_id']} (A vs C)")
    await assert_no_match(ws_b, "B", 2)

    for ws in (ws_a, ws_b, ws_c):
        await ws.close()
    print("\nblock demo OK: block excluded the pair, queue order preserved, C matched A")


if __name__ == "__main__":
    asyncio.run(main())
