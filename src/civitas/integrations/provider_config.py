"""Atomic local persistence for secret-free provider configuration."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pydantic import ValidationError

from civitas.contracts.provider_config import (
    CapabilityMapping,
    LocalProviderConfiguration,
)
from civitas.integrations.mcp import MCPAccessError

_MAX_MAPPING_BYTES = 256 * 1024


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


def load_mappings(
    store: LocalProviderConfigStore,
    configuration: LocalProviderConfiguration,
) -> dict[str, CapabilityMapping]:
    """Load unique, versioned mappings without escaping the config directory."""

    base_directory = store.path.parent.resolve()
    mappings: dict[str, CapabilityMapping] = {}
    references = {
        binding.mapping_file
        for binding in configuration.bindings
        if binding.mapping_file is not None
    }
    for reference in sorted(references):
        mapping_path = (base_directory / reference).resolve()
        if not mapping_path.is_relative_to(base_directory):
            raise MCPAccessError("mapping files must stay inside the configuration directory")
        try:
            if not mapping_path.is_file():
                raise MCPAccessError("configured mapping file is unavailable")
            if mapping_path.stat().st_size > _MAX_MAPPING_BYTES:
                raise MCPAccessError("configured mapping file exceeds the size limit")
            payload = mapping_path.read_text(encoding="utf-8")
            mappings[reference] = CapabilityMapping.model_validate_json(payload)
        except MCPAccessError:
            raise
        except (OSError, UnicodeError, ValidationError) as error:
            raise MCPAccessError("configured mapping file is invalid") from error
    return mappings
