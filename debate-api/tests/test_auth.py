import uuid

from tests.conftest import auth_device, run_sql


def test_device_auth_mints_valid_token(client):
    auth = auth_device(client)
    uuid.UUID(auth["user_id"])
    resp = client.get("/topics", headers={"Authorization": f"Bearer {auth['token']}"})
    assert resp.status_code == 200


def test_device_auth_idempotent(client):
    device_id = str(uuid.uuid4())
    first = auth_device(client, device_id)
    second = auth_device(client, device_id)
    assert first["user_id"] == second["user_id"]


def test_under_18_rejected(client):
    resp = client.post("/auth/device", json={"device_id": str(uuid.uuid4()), "over_18": False})
    assert resp.status_code == 403


def test_banned_user_rejected(client, migrated):
    device_id = str(uuid.uuid4())
    auth = auth_device(client, device_id)
    run_sql(
        migrated, "UPDATE users SET banned = true WHERE id = :uid", {"uid": auth["user_id"]}
    )
    resp = client.post("/auth/device", json={"device_id": device_id, "over_18": True})
    assert resp.status_code == 403
    # Existing token stops working too.
    resp = client.get("/topics", headers={"Authorization": f"Bearer {auth['token']}"})
    assert resp.status_code == 403


def test_routes_require_auth(client):
    assert client.get("/topics").status_code == 401
    assert client.post(f"/matches/{uuid.uuid4()}/end").status_code == 401
    assert client.get("/topics", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401


def test_topics_shape(client, topic_ids):
    auth = auth_device(client)
    topics = client.get("/topics", headers={"Authorization": f"Bearer {auth['token']}"}).json()
    assert len(topics) == len(topic_ids)
    assert topics[0] == {"id": topic_ids[0], "title": "Gun control"}


def test_healthz_no_auth(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
