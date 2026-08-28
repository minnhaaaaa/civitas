from pathlib import Path

from civitas.runtime.health import EXPECTED_DATABASE_REVISION

ROOT = Path(__file__).parents[2]


def test_container_includes_all_runtime_health_and_provider_scripts() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY scripts ./scripts" in dockerfile
    assert "USER civitas" in dockerfile
    assert "EXPOSE 8000 8001" in dockerfile


def test_kubernetes_base_is_secret_free_and_hardened() -> None:
    kustomization = (ROOT / "deploy/kubernetes/base/kustomization.yaml").read_text(encoding="utf-8")
    manifests = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "deploy/kubernetes/base").glob("*.yaml")
    )
    assert "secret.example.yaml" not in kustomization
    assert "readOnlyRootFilesystem: true" in manifests
    assert 'drop: ["ALL"]' in manifests
    assert "automountServiceAccountToken: false" in manifests
    assert "/health/ready" in manifests


def test_backup_and_restore_assets_are_guarded() -> None:
    backup = (ROOT / "scripts/backup_postgres.sh").read_text(encoding="utf-8")
    restore = (ROOT / "scripts/restore_postgres.sh").read_text(encoding="utf-8")
    assert "pg_dump" in backup and "sha256sum" in backup and "alembic" in backup
    assert "refuses destructive replacement" in restore
    assert "pg_restore" in restore and "sha256sum" in restore


def test_runtime_readiness_revision_matches_alembic_head() -> None:
    migration = ROOT / f"alembic/versions/{EXPECTED_DATABASE_REVISION}_add_service_heartbeats.py"
    assert migration.exists()
    assert f'revision: str = "{EXPECTED_DATABASE_REVISION}"' in migration.read_text(
        encoding="utf-8"
    )
