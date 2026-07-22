from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import structlog
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger(__name__)

ASYNC_DRIVER = "postgresql+asyncpg"

# SQLAlchemy's asyncpg dialect forwards every URL query param straight into
# asyncpg.connect() as a keyword argument, so any param asyncpg doesn't accept
# is a TypeError at connect time. Keep only what asyncpg takes; libpq-only
# params (sslrootcert, channel_binding, connect_timeout, ...) are dropped, and
# libpq's sslmode is translated to asyncpg's equivalent `ssl`.
_ASYNCPG_QUERY_PARAMS = frozenset(
    {
        "ssl",
        "direct_tls",
        "target_session_attrs",
        "timeout",
        "command_timeout",
        "statement_cache_size",
        "max_cached_statement_lifetime",
        "max_cacheable_statement_size",
        "server_settings",
        "krbsrvname",
        "gsslib",
        "passfile",
        "service",
        "servicefile",
        # SQLAlchemy asyncpg dialect option, not asyncpg's own
        "prepared_statement_cache_size",
    }
)

# Defaults that are fine locally and fatal in production.
DEV_JWT_SECRETS = frozenset({"dev_jwt_secret_change_me_min_32_bytes_long"})
DEV_INTERNAL_KEYS = frozenset({"", "dev_internal_key_change_me"})
DEV_LIVEKIT_KEY = "devkey"
DEV_LIVEKIT_SECRET = "devsecret_change_me"
MIN_JWT_SECRET_CHARS = 32


def normalize_postgres_url(url: str) -> str:
    """Make a managed-Postgres URL safe for SQLAlchemy + asyncpg.

    - postgres:// and postgresql:// are rewritten to postgresql+asyncpg://
      (Railway, Heroku, Supabase, and friends all hand out the bare scheme).
    - sslmode=<mode> becomes ssl=<mode>; asyncpg understands the same mode
      names, so sslmode=require keeps working over TLS.
    - Any other libpq-only query param is dropped rather than passed through
      to asyncpg, which would raise TypeError on connect.

    Explicit non-asyncpg drivers (e.g. postgresql+psycopg://) are left alone.
    """
    if not url:
        return url

    split = urlsplit(url)
    scheme = ASYNC_DRIVER if split.scheme in ("postgres", "postgresql") else split.scheme

    kept: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key, value in parse_qsl(split.query, keep_blank_values=True):
        name = "ssl" if key == "sslmode" else key
        if name not in _ASYNCPG_QUERY_PARAMS or name in seen:
            continue  # unsupported by asyncpg, or an explicit ssl= already won
        seen.add(name)
        kept.append((name, value))

    return urlunsplit((scheme, split.netloc, split.path, urlencode(kept), split.fragment))


class Settings(BaseSettings):
    """Dev-friendly defaults matching debate-infra; override everything in prod."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: Literal["development", "production"] = "development"

    # Railway (and most managed Postgres) inject DATABASE_URL; POSTGRES_URL wins
    # when both are present.
    postgres_url: str = Field(
        default="postgresql+asyncpg://debate:debate@localhost:5432/debate",
        validation_alias=AliasChoices("postgres_url", "database_url"),
    )
    redis_url: str = "redis://localhost:6379/0"
    livekit_url: str = "ws://localhost:7880"
    livekit_api_key: str = DEV_LIVEKIT_KEY
    livekit_api_secret: str = DEV_LIVEKIT_SECRET
    # HS256 wants >= 32 bytes; keep that property when overriding.
    jwt_secret: str = "dev_jwt_secret_change_me_min_32_bytes_long"
    # Shared secret for service-to-service moderation events (debate-agent).
    internal_api_key: str = "dev_internal_key_change_me"
    cors_origins: str = "*"

    @field_validator("postgres_url")
    @classmethod
    def _normalize_postgres_url(cls, value: str) -> str:
        return normalize_postgres_url(value)

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def production_problems(self) -> list[str]:
        """Config that must never reach production. Empty list == good to go."""
        problems: list[str] = []

        if not self.jwt_secret:
            problems.append("JWT_SECRET is not set")
        elif len(self.jwt_secret) < MIN_JWT_SECRET_CHARS:
            problems.append(
                f"JWT_SECRET is only {len(self.jwt_secret)} characters "
                f"(minimum {MIN_JWT_SECRET_CHARS}) — generate one with: openssl rand -hex 32"
            )
        elif self.jwt_secret in DEV_JWT_SECRETS:
            problems.append(
                "JWT_SECRET is still the development default — generate one with: "
                "openssl rand -hex 32"
            )

        if self.livekit_api_key == DEV_LIVEKIT_KEY:
            problems.append(
                'LIVEKIT_API_KEY is still the dev server\'s "devkey" — use the '
                "LiveKit Cloud project key"
            )
        if self.livekit_api_secret == DEV_LIVEKIT_SECRET:
            problems.append(
                "LIVEKIT_API_SECRET is still the dev server's default — use the "
                "LiveKit Cloud project secret"
            )

        if self.internal_api_key in DEV_INTERNAL_KEYS:
            problems.append(
                "INTERNAL_API_KEY is not set (debate-agent's moderation events would "
                "be unauthenticated) — generate one with: openssl rand -hex 32"
            )

        return problems

    def enforce_production_guards(self) -> None:
        """Refuse to boot a production deploy with development credentials."""
        if not self.is_production:
            return
        problems = self.production_problems()
        if not problems:
            return
        for problem in problems:
            logger.error("fatal_config_error", problem=problem, env=self.env)
        raise RuntimeError(
            "refusing to start with ENV=production: " + "; ".join(problems)
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
