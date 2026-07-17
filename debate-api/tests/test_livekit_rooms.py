"""Room creation at match time: metadata contract, idempotency, failure isolation."""

import json
from types import SimpleNamespace

from app.livekit_rooms import http_url
from tests.conftest import auth_device
from tests.test_ws import connect, ws_join


class FakeRoomService:
    def __init__(self, always_exists=False, create_error=None):
        self.create_calls = []
        self._always_exists = always_exists
        self._create_error = create_error

    async def list_rooms(self, req):
        rooms = [SimpleNamespace(name=n) for n in req.names] if self._always_exists else []
        return SimpleNamespace(rooms=rooms)

    async def create_room(self, req):
        self.create_calls.append(req)
        if self._create_error is not None:
            raise self._create_error
        return SimpleNamespace(name=req.name)


def install_fake_lk(client, **kwargs):
    fake = SimpleNamespace(room=FakeRoomService(**kwargs))
    client.app.state.lkapi = fake
    return fake


def run_match(client, topic_id, mode_pro, mode_con):
    auth_pro, auth_con = auth_device(client), auth_device(client)
    with connect(client, auth_pro) as wa, connect(client, auth_con) as wb:
        ws_join(wa, topic_id, "pro", mode_pro)
        assert wa.receive_json() == {"type": "queued"}
        ws_join(wb, topic_id, "con", mode_con)
        assert wb.receive_json() == {"type": "queued"}
        ma, mb = wa.receive_json(), wb.receive_json()
    return auth_pro, auth_con, ma, mb


def test_room_created_with_auto_metadata(client, topic_ids):
    fake = install_fake_lk(client)
    tid = topic_ids[0]
    auth_pro, auth_con, ma, mb = run_match(client, tid, "auto", "auto")
    assert ma["type"] == mb["type"] == "match_found"

    assert len(fake.room.create_calls) == 1  # exactly once per match
    req = fake.room.create_calls[0]
    assert req.name == ma["room_name"] == f"match_{ma['match_id']}"
    assert req.empty_timeout == 300
    assert json.loads(req.metadata) == {
        "match_id": ma["match_id"],
        "topic_id": tid,
        "topic": "Gun control",
        "fact_check_mode": "auto",
        "user_pro": auth_pro["user_id"],
        "user_con": auth_con["user_id"],
    }


def test_room_metadata_resolves_mixed_mode_to_on_demand(client, topic_ids):
    fake = install_fake_lk(client)
    auth_pro, auth_con, ma, _ = run_match(client, topic_ids[0], "auto", "on_demand")

    assert len(fake.room.create_calls) == 1
    meta = json.loads(fake.room.create_calls[0].metadata)
    assert meta["fact_check_mode"] == "on_demand"
    assert meta["match_id"] == ma["match_id"]
    assert meta["user_pro"] == auth_pro["user_id"]
    assert meta["user_con"] == auth_con["user_id"]


def test_room_creation_failure_does_not_block_match(client, topic_ids):
    fake = install_fake_lk(client, create_error=RuntimeError("livekit down"))
    _, _, ma, mb = run_match(client, topic_ids[0], "on_demand", "on_demand")

    # The exception was raised inside create_room and swallowed by the app.
    assert len(fake.room.create_calls) == 1
    assert ma["type"] == mb["type"] == "match_found"
    assert ma["match_id"] == mb["match_id"]
    assert ma["livekit_token"] and mb["livekit_token"]


def test_existing_room_treated_as_success(client, topic_ids):
    fake = install_fake_lk(client, always_exists=True)
    _, _, ma, mb = run_match(client, topic_ids[0], "on_demand", "on_demand")

    assert fake.room.create_calls == []  # idempotent: no second create
    assert ma["type"] == mb["type"] == "match_found"


def test_http_url_conversion():
    assert http_url("ws://10.0.0.115:7880") == "http://10.0.0.115:7880"
    assert http_url("wss://livekit.example.com") == "https://livekit.example.com"
    assert http_url("https://already.http") == "https://already.http"
