"""Settings assembly, parsing and the production safety validators."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import INSECURE_DEV_SECRET_KEY, Settings, get_settings

STRONG_SECRET = "k7Qm2Zt9XpL4vR8sN6bY3wJ1hG5dF0cA7eU9iO2pS4tV6xZ8mB1nC3qW5eR7yT9u"


def test_database_uri_is_assembled_from_the_parts():
    settings = Settings(
        _env_file=None,
        database_url=None,
        # ``_env_file=None`` stops the .env file, not the environment: without
        # this, an exported ``TEST_DATABASE_URL`` silently overrides the value
        # the test is deriving and the assertion below is about that export
        # rather than about the assembly.
        test_database_url=None,
        postgres_user="ada",
        # The value itself is the point: reserved characters must be percent-encoded.
        postgres_password="p@ss word",  # noqa: S106
        postgres_host="127.0.0.1",
        postgres_port=5433,
        postgres_db="nexus",
    )

    assert settings.sqlalchemy_database_uri == (
        "postgresql+psycopg://ada:p%40ss%20word@127.0.0.1:5433/nexus"
    )
    # The test URI is the same server, one database over.
    assert settings.test_sqlalchemy_database_uri.endswith("/nexus_test")


def test_an_explicit_database_url_wins_over_the_parts():
    explicit = "postgresql+psycopg://u:p@db.internal:5432/custom"
    settings = Settings(_env_file=None, database_url=explicit)

    assert settings.sqlalchemy_database_uri == explicit


def test_an_explicit_test_database_url_wins_over_the_parts():
    settings = Settings(
        _env_file=None,
        test_database_url="postgresql+psycopg://u:p@db.internal:5432/nexus_ci",
    )

    assert settings.test_sqlalchemy_database_uri == (
        "postgresql+psycopg://u:p@db.internal:5432/nexus_ci"
    )


def test_cors_origins_are_parsed_into_a_list():
    settings = Settings(
        _env_file=None,
        cors_origins="http://a.test, http://b.test ,,http://c.test,",
    )

    assert settings.cors_origin_list == [
        "http://a.test",
        "http://b.test",
        "http://c.test",
    ]


def test_cors_origins_default_to_the_vite_dev_servers():
    assert Settings(_env_file=None).cors_origin_list == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def test_blank_log_file_is_normalised_to_none():
    assert Settings(_env_file=None, log_file="   ").log_file is None


def test_environment_helpers_reflect_the_environment():
    production = Settings(
        _env_file=None, environment="production", secret_key=STRONG_SECRET, debug=False
    )
    assert production.is_production is True
    assert production.is_testing is False

    testing = Settings(_env_file=None, environment="test")
    assert testing.is_testing is True
    assert testing.is_production is False

    development = Settings(_env_file=None, environment="development", debug=True)
    assert development.is_production is False


def test_production_refuses_the_placeholder_secret_key():
    with pytest.raises(ValidationError, match="SECRET_KEY must be set"):
        Settings(
            _env_file=None,
            environment="production",
            secret_key=INSECURE_DEV_SECRET_KEY,
            debug=False,
        )


def test_production_refuses_debug():
    with pytest.raises(ValidationError, match="DEBUG must be false"):
        Settings(
            _env_file=None,
            environment="production",
            secret_key=STRONG_SECRET,
            debug=True,
        )


def test_production_accepts_a_strong_secret_and_no_debug():
    settings = Settings(
        _env_file=None,
        environment="production",
        secret_key=STRONG_SECRET,
        debug=False,
    )

    assert settings.is_production is True


def test_the_validators_only_apply_to_production():
    settings = Settings(_env_file=None, environment="development", debug=True)

    assert settings.secret_key == INSECURE_DEV_SECRET_KEY


def test_settings_are_built_from_environment_variables(make_settings):
    settings = make_settings(APP_NAME="NEXUS-CI", LOG_LEVEL="WARNING")

    assert settings.app_name == "NEXUS-CI"
    assert settings.log_level == "WARNING"


def test_the_settings_singleton_is_cached(settings):
    assert get_settings() is get_settings()


def test_the_settings_cache_is_cleared_after_a_test(settings, monkeypatch):
    before = get_settings().app_name

    monkeypatch.setenv("APP_NAME", "NEXUS-MUTATED")

    assert get_settings().app_name == before
