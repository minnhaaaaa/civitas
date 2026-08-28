import pytest

from civitas.application.audit_viewer import AuditCursorCodec, AuditCursorError
from civitas.runtime import RuntimeSettings, SettingsError


def test_audit_cursor_is_resource_and_link_bound() -> None:
    codec = AuditCursorCodec(b"s" * 32)
    cursor = codec.encode(link_id="link-1", resource="events", after=17)

    assert codec.decode(cursor, link_id="link-1", resource="events") == 17
    with pytest.raises(AuditCursorError, match="another resource"):
        codec.decode(cursor, link_id="link-1", resource="evidence")
    body, signature = cursor.split(".", 1)
    tampered = f"{'A' if body[0] != 'A' else 'B'}{body[1:]}.{signature}"
    with pytest.raises(AuditCursorError, match="invalid"):
        codec.decode(tampered, link_id="link-1", resource="events")


def _settings(**changes: object) -> RuntimeSettings:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://civitas:secret@database/civitas",
        "approval_secret_pepper": "a" * 32,
        "bearer_token": "b" * 32,
        "organization_id": "org-1",
        "operator_id": "operator-1",
        "operator_subject": "subject-1",
        "operator_roles": ("procurement-viewer",),
    }
    values.update(changes)
    return RuntimeSettings(**values)  # type: ignore[arg-type]


def test_audit_configuration_requires_independent_https_secret_in_production() -> None:
    with pytest.raises(SettingsError, match="requires"):
        _settings(audit_viewer_enabled=True)
    with pytest.raises(SettingsError, match="HTTPS"):
        _settings(
            environment="production",
            log_format="json",
            audit_viewer_enabled=True,
            audit_viewer_base_url="http://viewer.example",
            audit_link_secret="c" * 32,
        )
    with pytest.raises(SettingsError, match="independent"):
        _settings(
            environment="production",
            log_format="json",
            audit_viewer_enabled=True,
            audit_viewer_base_url="https://viewer.example",
            audit_link_secret="b" * 32,
        )
