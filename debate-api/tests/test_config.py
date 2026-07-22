"""Production config hardening: URL normalization and boot guards.

These are pure-config tests — no containers, no network.
"""

import inspect
import logging

import asyncpg
import pytest
from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg
from sqlalchemy.engine import make_url

from app.config import Settings, normalize_postgres_url

GOOD_SECRET = "a" * 64
GOOD_INTERNAL = "b" * 64


def prod_settings(**overrides) -> Settings:
    base = {
        "env": "production",
        "jwt_secret": GOOD_SECRET,
        "internal_api_key": GOOD_INTERNAL,
        "livekit_api_key": "APIrealkey",
        "livekit_api_secret": "realsecret",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


# ----------------------------------------------------------- URL normalization


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Railway/Heroku hand out the bare scheme; asyncpg needs the driver.
        ("postgres://u:p@h:5432/db", "postgresql+asyncpg://u:p@h:5432/db"),
        ("postgresql://u:p@h:5432/db", "postgresql+asyncpg://u:p@h:5432/db"),
        # Already correct — left alone.
        ("postgresql+asyncpg://u:p@h:5432/db", "postgresql+asyncpg://u:p@h:5432/db"),
        # libpq's sslmode has no asyncpg kwarg; ssl takes the same mode names.
        (
            "postgresql://u:p@h/db?sslmode=require",
            "postgresql+asyncpg://u:p@h/db?ssl=require",
        ),
        (
            "postgres://u:p@h/db?sslmode=verify-full",
            "postgresql+asyncpg://u:p@h/db?ssl=verify-full",
        ),
        # Other libpq-only params would be TypeErrors at connect time.
        (
            "postgres://u:p@h/db?sslmode=require&sslrootcert=/ca.pem&connect_timeout=10",
            "postgresql+asyncpg://u:p@h/db?ssl=require",
        ),
        ("postgresql://u:p@h/db?channel_binding=prefer", "postgresql+asyncpg://u:p@h/db"),
        # An explicit ssl= wins; the duplicate sslmode is dropped.
        (
            "postgresql://u:p@h/db?ssl=verify-full&sslmode=require",
            "postgresql+asyncpg://u:p@h/db?ssl=verify-full",
        ),
        # asyncpg-supported params survive.
        (
            "postgres://u:p@h/db?target_session_attrs=read-write",
            "postgresql+asyncpg://u:p@h/db?target_session_attrs=read-write",
        ),
        # Percent-encoded credentials must not be mangled.
        (
            "postgres://u:p%40ss%2Fword@h:5432/db?sslmode=require",
            "postgresql+asyncpg://u:p%40ss%2Fword@h:5432/db?ssl=require",
        ),
        ("", ""),
    ],
)
def test_normalize_postgres_url(raw, expected):
    assert normalize_postgres_url(raw) == expected


def test_non_asyncpg_driver_is_left_alone():
    # Don't silently rewrite a deliberate driver choice.
    url = "postgresql+psycopg://u:p@h/db"
    assert normalize_postgres_url(url) == url


def test_normalized_url_only_yields_kwargs_asyncpg_accepts():
    """The whole point of normalization: SQLAlchemy forwards every query param
    to asyncpg.connect(), so a leftover libpq param is a TypeError in prod."""
    accepted = set(inspect.signature(asyncpg.connect).parameters)
    raw = "postgres://u:p@h:5432/db?sslmode=require&sslrootcert=/ca.pem&connect_timeout=10"

    _, opts = PGDialect_asyncpg().create_connect_args(make_url(normalize_postgres_url(raw)))

    assert opts["ssl"] == "require"
    unsupported = set(opts) - accepted
    assert not unsupported, f"asyncpg.connect() would reject: {unsupported}"


def test_settings_normalizes_on_load():
    settings = Settings(_env_file=None, postgres_url="postgres://u:p@h/db?sslmode=require")
    assert settings.postgres_url == "postgresql+asyncpg://u:p@h/db?ssl=require"


def test_database_url_is_accepted_as_a_fallback(monkeypatch):
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@railway/db")
    assert Settings(_env_file=None).postgres_url == "postgresql+asyncpg://u:p@railway/db"


def test_postgres_url_wins_over_database_url(monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", "postgres://explicit@h/db")
    monkeypatch.setenv("DATABASE_URL", "postgres://fallback@h/db")
    assert "explicit" in Settings(_env_file=None).postgres_url


# ------------------------------------------------------------ production guards


def test_development_tolerates_every_dev_default():
    settings = Settings(_env_file=None)  # all defaults
    assert settings.is_production is False
    settings.enforce_production_guards()  # must not raise


def test_production_accepts_real_credentials():
    prod_settings().enforce_production_guards()  # must not raise


@pytest.mark.parametrize(
    ("overrides", "expected_fragment"),
    [
        ({"jwt_secret": ""}, "JWT_SECRET is not set"),
        ({"jwt_secret": "short"}, "only 5 characters"),
        (
            {"jwt_secret": "dev_jwt_secret_change_me_min_32_bytes_long"},
            "JWT_SECRET is still the development default",
        ),
        ({"livekit_api_key": "devkey"}, "LIVEKIT_API_KEY"),
        ({"livekit_api_secret": "devsecret_change_me"}, "LIVEKIT_API_SECRET"),
        ({"internal_api_key": ""}, "INTERNAL_API_KEY is not set"),
        ({"internal_api_key": "dev_internal_key_change_me"}, "INTERNAL_API_KEY is not set"),
    ],
)
def test_production_guard_refuses(overrides, expected_fragment):
    settings = prod_settings(**overrides)
    with pytest.raises(RuntimeError) as excinfo:
        settings.enforce_production_guards()
    assert expected_fragment in str(excinfo.value)
    assert "ENV=production" in str(excinfo.value)


def test_guard_reports_every_problem_at_once():
    settings = prod_settings(jwt_secret="short", livekit_api_key="devkey", internal_api_key="")
    problems = settings.production_problems()
    assert len(problems) == 3


def test_guard_message_tells_you_how_to_fix_it():
    settings = prod_settings(jwt_secret="short")
    assert "openssl rand -hex 32" in settings.production_problems()[0]


def test_create_app_refuses_production_with_dev_secrets(monkeypatch):
    from app.main import create_app

    with pytest.raises(RuntimeError):
        create_app(prod_settings(livekit_api_key="devkey"))


# ------------------------------------------------------------------- logging


def test_logging_never_writes_to_a_file():
    """Container logs must be JSON on stdout — never a file inside the image.

    pytest's own logging plugin attaches a /dev/null FileHandler, so only
    handlers pointed at a real path count as a violation.
    """
    import os

    from app.logging_config import configure_logging

    configure_logging()
    on_disk = [
        h
        for h in logging.getLogger().handlers
        if isinstance(h, logging.FileHandler)
        and os.path.realpath(h.baseFilename) != os.path.realpath(os.devnull)
    ]
    assert not on_disk, f"logs would be written to files: {on_disk}"


def test_structlog_renders_json_to_stdout():
    import sys

    import structlog

    from app.logging_config import configure_logging

    configure_logging()
    config = structlog.get_config()
    factory = config["logger_factory"]
    assert isinstance(factory, structlog.PrintLoggerFactory)
    assert factory._file is sys.stdout
    assert any(
        isinstance(p, structlog.processors.JSONRenderer) for p in config["processors"]
    )
