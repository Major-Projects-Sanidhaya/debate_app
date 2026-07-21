"""Reports, blocks, internal moderation events, and banned surfaces."""

import asyncio
import uuid
from types import SimpleNamespace

from sqlalchemy import text

from tests.conftest import auth_device, run_sql
from tests.test_ws import connect

INTERNAL_KEY = "dev_internal_key_change_me"  # settings-fixture default


# --- helpers -----------------------------------------------------------------


class FakeModRoomService:
    def __init__(self):
        self.deleted = []

    async def delete_room(self, req):
        self.deleted.append(req.room)
        return SimpleNamespace()


def install_fake_lk(client):
    fake = SimpleNamespace(room=FakeModRoomService())
    client.app.state.lkapi = fake
    return fake


def insert_match(migrated, topic_id, user_pro, user_con) -> str:
    match_id = str(uuid.uuid4())
    run_sql(
        migrated,
        "INSERT INTO matches (id, topic_id, user_pro, user_con, fact_check_mode) "
        "VALUES (:id, :tid, :pro, :con, 'on_demand')",
        {"id": match_id, "tid": topic_id, "pro": user_pro, "con": user_con},
    )
    return match_id


def scalar_sql(pg_url, statement, params=None):
    async def _run():
        from app.db import make_engine_and_sessionmaker

        engine, sessionmaker = make_engine_and_sessionmaker(pg_url)
        async with sessionmaker() as session:
            result = await session.execute(text(statement), params or {})
            row = result.first()
        await engine.dispose()
        return row[0] if row else None

    return asyncio.run(_run())


def banned(migrated, user_id):
    return scalar_sql(migrated, "SELECT banned FROM users WHERE id = :id", {"id": user_id})


def flagged(migrated, user_id):
    return scalar_sql(migrated, "SELECT flagged FROM users WHERE id = :id", {"id": user_id})


def ended_reason(migrated, match_id):
    return scalar_sql(migrated, "SELECT ended_reason FROM matches WHERE id = :m", {"m": match_id})


def hdr(auth):
    return {"Authorization": f"Bearer {auth['token']}"}


def report(client, match_id, auth, reason, details=None):
    body = {"reason": reason}
    if details is not None:
        body["details"] = details
    return client.post(f"/matches/{match_id}/report", json=body, headers=hdr(auth))


# --- reports -----------------------------------------------------------------


def test_report_participant_only(client, migrated, topic_ids):
    pro, con, outsider = auth_device(client), auth_device(client), auth_device(client)
    match_id = insert_match(migrated, topic_ids[0], pro["user_id"], con["user_id"])

    assert report(client, match_id, pro, "harassment").status_code == 204
    assert report(client, match_id, outsider, "harassment").status_code == 403
    # Unknown match is 404, not 403.
    assert report(client, str(uuid.uuid4()), pro, "harassment").status_code == 404
    # Auth required.
    unauthed = client.post(f"/matches/{match_id}/report", json={"reason": "spam_other"})
    assert unauthed.status_code == 401


def test_report_idempotent_per_reporter_match(client, migrated, topic_ids):
    pro, con = auth_device(client), auth_device(client)
    match_id = insert_match(migrated, topic_ids[0], pro["user_id"], con["user_id"])

    assert report(client, match_id, pro, "harassment").status_code == 204
    assert report(client, match_id, pro, "hate_speech", "again").status_code == 204  # repeat ok

    count = scalar_sql(
        migrated,
        "SELECT count(*) FROM reports WHERE reporter_id = :r AND match_id = :m",
        {"r": pro["user_id"], "m": match_id},
    )
    assert count == 1  # no duplicate row
    # First write wins; the repeat did not overwrite.
    reason = scalar_sql(
        migrated, "SELECT reason FROM reports WHERE match_id = :m", {"m": match_id}
    )
    assert reason == "harassment"


def test_report_details_length_validated(client, migrated, topic_ids):
    pro, con = auth_device(client), auth_device(client)
    match_id = insert_match(migrated, topic_ids[0], pro["user_id"], con["user_id"])
    assert report(client, match_id, pro, "spam_other", "x" * 500).status_code == 204
    con2 = auth_device(client)
    match2 = insert_match(migrated, topic_ids[0], con2["user_id"], pro["user_id"])
    assert report(client, match2, con2, "spam_other", "x" * 501).status_code == 422


def test_invalid_reason_rejected(client, migrated, topic_ids):
    pro, con = auth_device(client), auth_device(client)
    match_id = insert_match(migrated, topic_ids[0], pro["user_id"], con["user_id"])
    assert report(client, match_id, pro, "not_a_reason").status_code == 422


# --- auto-ban ----------------------------------------------------------------


def _report_from_n_distinct(client, migrated, topic_id, offender, n):
    """n distinct reporters, each in their own match with the offender, report them."""
    for _ in range(n):
        reporter = auth_device(client)
        match_id = insert_match(migrated, topic_id, reporter["user_id"], offender["user_id"])
        assert report(client, match_id, reporter, "harassment").status_code == 204


def test_auto_ban_at_three_distinct_reporters(client, migrated, topic_ids):
    offender = auth_device(client)
    _report_from_n_distinct(client, migrated, topic_ids[0], offender, 3)
    assert banned(migrated, offender["user_id"]) is True


def test_no_auto_ban_at_two_distinct_reporters(client, migrated, topic_ids):
    offender = auth_device(client)
    _report_from_n_distinct(client, migrated, topic_ids[0], offender, 2)
    assert banned(migrated, offender["user_id"]) is False


def test_same_reporter_repeated_does_not_auto_ban(client, migrated, topic_ids):
    # One reporter across multiple matches is still ONE distinct reporter.
    offender = auth_device(client)
    reporter = auth_device(client)
    for _ in range(4):
        match_id = insert_match(migrated, topic_ids[0], reporter["user_id"], offender["user_id"])
        assert report(client, match_id, reporter, "harassment").status_code == 204
    assert banned(migrated, offender["user_id"]) is False


# --- immediate-action reports ------------------------------------------------


def test_underage_report_deletes_room_and_flags(client, migrated, topic_ids):
    fake = install_fake_lk(client)
    reporter, offender = auth_device(client), auth_device(client)
    match_id = insert_match(migrated, topic_ids[0], reporter["user_id"], offender["user_id"])

    assert report(client, match_id, reporter, "underage").status_code == 204

    assert fake.room.deleted == [f"match_{match_id}"]
    assert ended_reason(migrated, match_id) == "moderation"
    assert flagged(migrated, offender["user_id"]) is True
    # The reporter is not flagged.
    assert flagged(migrated, reporter["user_id"]) is False


def test_sexual_content_report_deletes_room_and_flags(client, migrated, topic_ids):
    fake = install_fake_lk(client)
    reporter, offender = auth_device(client), auth_device(client)
    match_id = insert_match(migrated, topic_ids[0], reporter["user_id"], offender["user_id"])

    assert report(client, match_id, reporter, "sexual_content").status_code == 204
    assert fake.room.deleted == [f"match_{match_id}"]
    assert ended_reason(migrated, match_id) == "moderation"


def test_ordinary_report_does_not_delete_room(client, migrated, topic_ids):
    fake = install_fake_lk(client)
    reporter, offender = auth_device(client), auth_device(client)
    match_id = insert_match(migrated, topic_ids[0], reporter["user_id"], offender["user_id"])

    assert report(client, match_id, reporter, "harassment").status_code == 204
    assert fake.room.deleted == []
    assert ended_reason(migrated, match_id) is None


# --- blocks ------------------------------------------------------------------


def test_block_participant_only(client, migrated, topic_ids):
    pro, con, outsider = auth_device(client), auth_device(client), auth_device(client)
    match_id = insert_match(migrated, topic_ids[0], pro["user_id"], con["user_id"])
    assert client.post(f"/matches/{match_id}/block", headers=hdr(outsider)).status_code == 403
    assert client.post(f"/matches/{match_id}/block").status_code == 401


def test_block_writes_row_and_redis_set(client, migrated, topic_ids, sync_redis_client):
    pro, con = auth_device(client), auth_device(client)
    match_id = insert_match(migrated, topic_ids[0], pro["user_id"], con["user_id"])

    assert client.post(f"/matches/{match_id}/block", headers=hdr(pro)).status_code == 204

    count = scalar_sql(
        migrated,
        "SELECT count(*) FROM blocks WHERE blocker_id = :b AND blocked_id = :d",
        {"b": pro["user_id"], "d": con["user_id"]},
    )
    assert count == 1
    # One-directional Redis set: blocker's set holds the blocked id.
    assert sync_redis_client.sismember(f"blocks:{pro['user_id']}", con["user_id"])
    assert not sync_redis_client.exists(f"blocks:{con['user_id']}")


def test_block_idempotent(client, migrated, topic_ids, sync_redis_client):
    pro, con = auth_device(client), auth_device(client)
    match_id = insert_match(migrated, topic_ids[0], pro["user_id"], con["user_id"])
    assert client.post(f"/matches/{match_id}/block", headers=hdr(pro)).status_code == 204
    assert client.post(f"/matches/{match_id}/block", headers=hdr(pro)).status_code == 204
    count = scalar_sql(
        migrated, "SELECT count(*) FROM blocks WHERE blocker_id = :b", {"b": pro["user_id"]}
    )
    assert count == 1


# --- internal moderation events ----------------------------------------------


def post_event(client, body, key=INTERNAL_KEY):
    headers = {"X-Internal-Key": key} if key is not None else {}
    return client.post("/internal/moderation/events", json=body, headers=headers)


def make_event(match_id, stance="pro", severity="medium", category="harassment_hate"):
    return {
        "match_id": match_id,
        "source": "transcript",
        "stance": stance,
        "category": category,
        "severity": severity,
        "excerpt": "some flagged text",
        "ts": 1_700_000_000,
    }


def test_internal_event_requires_key(client, migrated, topic_ids):
    pro, con = auth_device(client), auth_device(client)
    match_id = insert_match(migrated, topic_ids[0], pro["user_id"], con["user_id"])
    assert post_event(client, make_event(match_id), key=None).status_code == 401
    assert post_event(client, make_event(match_id), key="wrong").status_code == 401


def test_internal_event_medium_stores_only(client, migrated, topic_ids):
    fake = install_fake_lk(client)
    pro, con = auth_device(client), auth_device(client)
    match_id = insert_match(migrated, topic_ids[0], pro["user_id"], con["user_id"])

    resp = post_event(client, make_event(match_id, stance="pro", severity="medium"))
    assert resp.status_code == 204

    assert fake.room.deleted == []  # no room deletion for medium
    assert ended_reason(migrated, match_id) is None
    assert flagged(migrated, pro["user_id"]) is False
    # The event row is stored, attributed to the pro user.
    stored_user = scalar_sql(
        migrated, "SELECT user_id FROM moderation_events WHERE match_id = :m", {"m": match_id}
    )
    assert str(stored_user) == pro["user_id"]


def test_internal_event_severe_deletes_room_and_flags(client, migrated, topic_ids):
    fake = install_fake_lk(client)
    pro, con = auth_device(client), auth_device(client)
    match_id = insert_match(migrated, topic_ids[0], pro["user_id"], con["user_id"])

    # stance=con → the con user is the offender.
    resp = post_event(client, make_event(match_id, stance="con", severity="severe"))
    assert resp.status_code == 204

    assert fake.room.deleted == [f"match_{match_id}"]
    assert ended_reason(migrated, match_id) == "moderation"
    assert flagged(migrated, con["user_id"]) is True  # stance=con → con user
    assert flagged(migrated, pro["user_id"]) is False


def test_internal_event_unknown_match_404(client):
    assert post_event(client, make_event(str(uuid.uuid4()))).status_code == 404


# --- banned WS surface -------------------------------------------------------


def test_banned_ws_join_gets_account_suspended(client, migrated, topic_ids):
    auth = auth_device(client)
    run_sql(migrated, "UPDATE users SET banned = true WHERE id = :id", {"id": auth["user_id"]})
    with connect(client, auth) as ws:
        msg = ws.receive_json()
        assert msg == {"type": "error", "message": "account_suspended"}
