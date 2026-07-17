import time
import uuid

import jwt

from tests.conftest import TEST_LIVEKIT_SECRET, auth_device


def ws_join(ws, topic_id, stance, mode="on_demand"):
    ws.send_json(
        {"type": "join", "topic_id": topic_id, "stance": stance, "fact_check_mode": mode}
    )


def connect(client, auth):
    return client.websocket_connect(f"/ws/match?token={auth['token']}")


def test_opposite_stances_match(client, topic_ids, settings):
    tid = topic_ids[0]
    auth_a, auth_b = auth_device(client), auth_device(client)
    with connect(client, auth_a) as wa, connect(client, auth_b) as wb:
        ws_join(wa, tid, "pro")
        assert wa.receive_json() == {"type": "queued"}
        ws_join(wb, tid, "con")
        assert wb.receive_json() == {"type": "queued"}

        ma, mb = wa.receive_json(), wb.receive_json()

    assert ma["type"] == mb["type"] == "match_found"
    assert ma["match_id"] == mb["match_id"]
    assert ma["room_name"] == f"match_{ma['match_id']}"
    assert ma["livekit_url"] == settings.livekit_url
    assert ma["topic"] == {"id": tid, "title": "Gun control"}
    assert (ma["your_stance"], ma["peer_stance"]) == ("pro", "con")
    assert (mb["your_stance"], mb["peer_stance"]) == ("con", "pro")
    assert ma["fact_check_mode"] == mb["fact_check_mode"] == "on_demand"

    # LiveKit tokens must be valid for the right identity and room, ~2h TTL.
    for m, auth in ((ma, auth_a), (mb, auth_b)):
        claims = jwt.decode(m["livekit_token"], TEST_LIVEKIT_SECRET, algorithms=["HS256"])
        assert claims["sub"] == auth["user_id"]
        assert claims["video"]["room"] == m["room_name"]
        assert claims["video"]["roomJoin"] is True
        assert claims["exp"] - time.time() > 7000


def test_auto_auto_resolves_auto(client, topic_ids):
    tid = topic_ids[0]
    with connect(client, auth_device(client)) as wa, connect(client, auth_device(client)) as wb:
        ws_join(wa, tid, "pro", "auto")
        assert wa.receive_json() == {"type": "queued"}
        ws_join(wb, tid, "con", "auto")
        assert wb.receive_json() == {"type": "queued"}
        assert wa.receive_json()["fact_check_mode"] == "auto"
        assert wb.receive_json()["fact_check_mode"] == "auto"


def test_auto_on_demand_resolves_on_demand(client, topic_ids):
    tid = topic_ids[0]
    with connect(client, auth_device(client)) as wa, connect(client, auth_device(client)) as wb:
        ws_join(wa, tid, "pro", "auto")
        assert wa.receive_json() == {"type": "queued"}
        ws_join(wb, tid, "con", "on_demand")
        assert wb.receive_json() == {"type": "queued"}
        assert wa.receive_json()["fact_check_mode"] == "on_demand"
        assert wb.receive_json()["fact_check_mode"] == "on_demand"


def test_double_join_rejected_ws(client, topic_ids):
    with connect(client, auth_device(client)) as ws:
        ws_join(ws, topic_ids[0], "pro")
        assert ws.receive_json() == {"type": "queued"}
        ws_join(ws, topic_ids[1], "con")
        err = ws.receive_json()
        assert err["type"] == "error"
        assert "already" in err["message"]


def test_disconnect_removes_from_queue(client, topic_ids, sync_redis_client):
    auth = auth_device(client)
    tid = topic_ids[0]
    with connect(client, auth) as ws:
        ws_join(ws, tid, "pro")
        assert ws.receive_json() == {"type": "queued"}
        assert sync_redis_client.llen(f"q:{tid}:pro") == 1

    deadline = time.time() + 2
    while time.time() < deadline:
        if (
            sync_redis_client.llen(f"q:{tid}:pro") == 0
            and not sync_redis_client.exists(f"inq:{auth['user_id']}")
        ):
            break
        time.sleep(0.05)
    assert sync_redis_client.llen(f"q:{tid}:pro") == 0
    assert not sync_redis_client.exists(f"inq:{auth['user_id']}")


def test_cancel_removes_from_queue(client, topic_ids, sync_redis_client):
    auth = auth_device(client)
    tid = topic_ids[0]
    with connect(client, auth) as ws:
        ws_join(ws, tid, "pro")
        assert ws.receive_json() == {"type": "queued"}
        ws.send_json({"type": "cancel"})
        # No ack for cancel in the contract; verify via a re-join being accepted.
        ws_join(ws, tid, "pro")
        assert ws.receive_json() == {"type": "queued"}
    assert True


def test_invalid_token_rejected_ws(client):
    with client.websocket_connect("/ws/match?token=garbage") as ws:
        err = ws.receive_json()
        assert err["type"] == "error"


def test_unknown_topic_errors(client):
    with connect(client, auth_device(client)) as ws:
        ws_join(ws, 999999, "pro")
        err = ws.receive_json()
        assert err["type"] == "error"
        assert "topic" in err["message"]


def test_end_match_flow(client, topic_ids):
    tid = topic_ids[0]
    auth_a, auth_b, auth_c = auth_device(client), auth_device(client), auth_device(client)
    with connect(client, auth_a) as wa, connect(client, auth_b) as wb:
        ws_join(wa, tid, "pro")
        assert wa.receive_json() == {"type": "queued"}
        ws_join(wb, tid, "con")
        assert wb.receive_json() == {"type": "queued"}
        match_id = wa.receive_json()["match_id"]
        wb.receive_json()

    hdr = {"Authorization": f"Bearer {auth_a['token']}"}
    assert client.post(f"/matches/{match_id}/end", headers=hdr).status_code == 204
    # Idempotent, and callable by the other participant too.
    assert client.post(f"/matches/{match_id}/end", headers=hdr).status_code == 204
    hdr_b = {"Authorization": f"Bearer {auth_b['token']}"}
    assert client.post(f"/matches/{match_id}/end", headers=hdr_b).status_code == 204
    # Non-participant is rejected; unknown match is 404.
    hdr_c = {"Authorization": f"Bearer {auth_c['token']}"}
    assert client.post(f"/matches/{match_id}/end", headers=hdr_c).status_code == 403
    assert client.post(f"/matches/{uuid.uuid4()}/end", headers=hdr).status_code == 404
