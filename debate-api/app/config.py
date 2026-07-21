from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Dev-friendly defaults matching debate-infra; override everything in prod."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_url: str = "postgresql+asyncpg://debate:debate@localhost:5432/debate"
    redis_url: str = "redis://localhost:6379/0"
    livekit_url: str = "ws://localhost:7880"
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "devsecret_change_me"
    # HS256 wants >= 32 bytes; keep that property when overriding.
    jwt_secret: str = "dev_jwt_secret_change_me_min_32_bytes_long"
    # Shared secret for service-to-service moderation events (debate-agent).
    internal_api_key: str = "dev_internal_key_change_me"
    cors_origins: str = "*"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
