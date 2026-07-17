from datetime import timedelta

from livekit import api

from app.config import Settings

TOKEN_TTL = timedelta(hours=2)


def mint_livekit_token(settings: Settings, identity: str, name: str, room_name: str) -> str:
    return (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(identity)
        .with_name(name)
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
        .with_ttl(TOKEN_TTL)
        .to_jwt()
    )
