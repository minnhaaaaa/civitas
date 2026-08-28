from dataclasses import replace

import pytest

from civitas.runtime import RuntimeSettings, SettingsError


def _settings() -> RuntimeSettings:
    return RuntimeSettings(
        database_url="postgresql+psycopg://civitas:strong-password@database/civitas",
        approval_secret_pepper="a" * 32,
        bearer_token="b" * 32,
        organization_id="org-1",
        operator_id="operator-1",
        operator_subject="subject-1",
        operator_roles=("procurement-operator",),
    )


def test_production_configuration_requires_safe_transport_and_logging() -> None:
    with pytest.raises(SettingsError, match="Streamable HTTP"):
        replace(_settings(), environment="production", transport="stdio", log_format="json")
    with pytest.raises(SettingsError, match="LOG_FORMAT=json"):
        replace(_settings(), environment="production")


def test_live_provider_mode_requires_explicit_factory() -> None:
    with pytest.raises(SettingsError, match="PROVIDER_FACTORY"):
        replace(_settings(), live_provider_required=True)
    with pytest.raises(SettingsError, match="module:callable"):
        replace(_settings(), provider_factory="provider.factory")


def test_production_rejects_checked_in_local_secret_examples() -> None:
    with pytest.raises(SettingsError, match="development value"):
        replace(
            _settings(),
            environment="production",
            log_format="json",
            bearer_token="local-bearer-token-change-this-value-now",
        )


def test_secret_files_are_supported_without_parallel_plaintext_values(tmp_path: object) -> None:
    root = tmp_path  # pytest's Path fixture is intentionally kept local to this test.
    database = root / "database"  # type: ignore[operator]
    approval = root / "approval"  # type: ignore[operator]
    bearer = root / "bearer"  # type: ignore[operator]
    database.write_text(
        "postgresql+psycopg://civitas:strong-password@database/civitas\n",
        encoding="utf-8",
    )
    approval.write_text("a" * 32, encoding="utf-8")
    bearer.write_text("b" * 32, encoding="utf-8")

    settings = RuntimeSettings.from_env(
        {
            "DATABASE_URL_FILE": str(database),
            "CIVITAS_APPROVAL_SECRET_PEPPER_FILE": str(approval),
            "CIVITAS_BEARER_TOKEN_FILE": str(bearer),
            "CIVITAS_ORGANIZATION_ID": "org-1",
            "CIVITAS_OPERATOR_ID": "operator-1",
        }
    )

    assert settings.database_url.endswith("/civitas")
    assert settings.approval_secret_pepper == "a" * 32
    assert settings.bearer_token == "b" * 32


def test_plaintext_and_file_secret_cannot_both_be_set(tmp_path: object) -> None:
    secret = tmp_path / "database"  # type: ignore[operator]
    secret.write_text("ignored", encoding="utf-8")
    with pytest.raises(SettingsError, match="only one"):
        RuntimeSettings.from_env(
            {
                "DATABASE_URL": _settings().database_url,
                "DATABASE_URL_FILE": str(secret),
                "CIVITAS_APPROVAL_SECRET_PEPPER": "a" * 32,
                "CIVITAS_BEARER_TOKEN": "b" * 32,
                "CIVITAS_ORGANIZATION_ID": "org-1",
                "CIVITAS_OPERATOR_ID": "operator-1",
            }
        )
