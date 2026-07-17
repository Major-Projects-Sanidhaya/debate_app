import json

from pipeline.models import parse_room_metadata

VALID = {
    "match_id": "abc-123",
    "topic_id": 1,
    "topic": "Gun control",
    "fact_check_mode": "auto",
    "user_pro": "u1",
    "user_con": "u2",
}


def test_parses_valid_contract():
    meta = parse_room_metadata(json.dumps(VALID), room_name="match_abc-123")
    assert meta.match_id == "abc-123"
    assert meta.topic_id == 1
    assert meta.topic == "Gun control"
    assert meta.fact_check_mode == "auto"
    assert meta.user_pro == "u1"
    assert meta.user_con == "u2"


def test_missing_metadata_falls_back_to_on_demand_unknown():
    meta = parse_room_metadata(None, room_name="match_xyz")
    assert meta.fact_check_mode == "on_demand"
    assert meta.topic == "unknown"
    assert meta.match_id == "xyz"  # derived from the room name
    assert meta.user_pro is None and meta.user_con is None


def test_empty_string_metadata_falls_back():
    meta = parse_room_metadata("", room_name="match_xyz")
    assert meta.fact_check_mode == "on_demand"


def test_invalid_json_falls_back():
    meta = parse_room_metadata("{not json", room_name="match_xyz")
    assert meta.fact_check_mode == "on_demand"
    assert meta.topic == "unknown"


def test_missing_keys_fall_back():
    meta = parse_room_metadata(json.dumps({"match_id": "x"}), room_name="match_x")
    assert meta.topic == "unknown"


def test_bad_mode_falls_back():
    bad = dict(VALID, fact_check_mode="always")
    meta = parse_room_metadata(json.dumps(bad), room_name="match_abc-123")
    assert meta.fact_check_mode == "on_demand"
