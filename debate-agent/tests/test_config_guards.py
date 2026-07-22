"""Production boot guards for the worker (mocked env only — no network)."""

import pytest

from config import AgentConfig

ENV_VARS = (
    "ENV",
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "DEEPGRAM_API_KEY",
    "LLM_PROVIDER",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "INTERNAL_API_KEY",
)


def prod_config(**overrides) -> AgentConfig:
    base = {
        "env": "production",
        "livekit_url": "wss://project.livekit.cloud",
        "livekit_api_key": "APIrealkey",
        "livekit_api_secret": "realsecret",
        "deepgram_api_key": "dg-real",
        "llm_provider": "gemini",
        "gemini_api_key": "gemini-real",
        "internal_api_key": "c" * 64,
    }
    base.update(overrides)
    return AgentConfig(**base)


@pytest.fixture
def clean_env(monkeypatch):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


# ------------------------------------------------------------------- from_env


def test_from_env_defaults(clean_env):
    config = AgentConfig.from_env()
    assert config.env == "development"
    assert config.llm_provider == "gemini"
    assert config.is_production is False


def test_from_env_reads_and_normalizes(clean_env):
    clean_env.setenv("ENV", "production")
    clean_env.setenv("LLM_PROVIDER", "  Anthropic  ")
    clean_env.setenv("DEEPGRAM_API_KEY", "  dg-key  ")
    config = AgentConfig.from_env()
    assert config.is_production is True
    assert config.llm_provider == "anthropic"
    assert config.deepgram_api_key == "dg-key"  # whitespace-only stays falsy


# ------------------------------------------------------------ guard behaviour


def test_development_tolerates_dev_credentials():
    config = AgentConfig(
        env="development", livekit_api_key="devkey", livekit_api_secret="devsecret_change_me"
    )
    config.enforce_production_guards()  # must not raise


def test_production_accepts_real_credentials():
    prod_config().enforce_production_guards()  # must not raise


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"livekit_api_key": "devkey"}, "LIVEKIT_API_KEY"),
        ({"livekit_api_secret": "devsecret_change_me"}, "LIVEKIT_API_SECRET"),
        ({"livekit_api_key": ""}, "LIVEKIT_API_KEY is not set"),
        ({"deepgram_api_key": ""}, "DEEPGRAM_API_KEY is not set"),
        ({"internal_api_key": ""}, "INTERNAL_API_KEY is not set"),
        ({"internal_api_key": "dev_internal_key_change_me"}, "INTERNAL_API_KEY is not set"),
    ],
)
def test_production_guard_refuses(overrides, fragment):
    with pytest.raises(RuntimeError) as excinfo:
        prod_config(**overrides).enforce_production_guards()
    assert fragment in str(excinfo.value)
    assert "ENV=production" in str(excinfo.value)


# ------------------------------------------- provider-conditional key requirement


def test_gemini_requires_gemini_key():
    config = prod_config(llm_provider="gemini", gemini_api_key="", anthropic_api_key="anth")
    with pytest.raises(RuntimeError) as excinfo:
        config.enforce_production_guards()
    assert "GEMINI_API_KEY is not set" in str(excinfo.value)
    assert "LLM_PROVIDER=gemini" in str(excinfo.value)


def test_anthropic_requires_anthropic_key():
    config = prod_config(llm_provider="anthropic", anthropic_api_key="", gemini_api_key="gem")
    with pytest.raises(RuntimeError) as excinfo:
        config.enforce_production_guards()
    assert "ANTHROPIC_API_KEY is not set" in str(excinfo.value)


def test_inactive_provider_key_is_not_required():
    # Running on gemini must not demand an Anthropic key, and vice versa.
    prod_config(
        llm_provider="gemini", gemini_api_key="gem", anthropic_api_key=""
    ).enforce_production_guards()
    prod_config(
        llm_provider="anthropic", anthropic_api_key="anth", gemini_api_key=""
    ).enforce_production_guards()


def test_unknown_provider_is_rejected():
    with pytest.raises(RuntimeError) as excinfo:
        prod_config(llm_provider="chatgpt").enforce_production_guards()
    assert "LLM_PROVIDER" in str(excinfo.value)


# ----------------------------------------------------------------- reporting


def test_all_problems_reported_at_once():
    config = prod_config(
        livekit_api_key="devkey", deepgram_api_key="", internal_api_key="", gemini_api_key=""
    )
    problems = config.production_problems()
    assert len(problems) == 4
    joined = " ".join(problems)
    for expected in ("LIVEKIT_API_KEY", "DEEPGRAM_API_KEY", "INTERNAL_API_KEY", "GEMINI_API_KEY"):
        assert expected in joined


def test_internal_key_message_points_at_debate_api():
    problem = prod_config(internal_api_key="").production_problems()[0]
    assert "debate-api" in problem
