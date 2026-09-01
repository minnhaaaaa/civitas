"""Atomic local persistence for secret-free provider configuration."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from civitas.contracts.provider_config import LocalProviderConfiguration


class LocalProviderConfigStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> LocalProviderConfiguration:
        if not self._path.exists():
            return LocalProviderConfiguration()
        return LocalProviderConfiguration.model_validate_json(
            self._path.read_text(encoding="utf-8")
        )

    def save(self, configuration: LocalProviderConfiguration) -> None:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = configuration.model_dump_json(indent=2) + "\n"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                os.chmod(temporary_path, 0o600)
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self._path)
            os.chmod(self._path, 0o600)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
