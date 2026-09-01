"""Persistence behavior for the local provider configuration file."""

from pathlib import Path

from civitas.contracts.provider_config import LocalProviderConfiguration, ProviderMode
from civitas.integrations.provider_config import LocalProviderConfigStore


def test_store_round_trips_configuration_with_owner_only_permissions(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "providers.json"
    store = LocalProviderConfigStore(path)
    expected = LocalProviderConfiguration(mode=ProviderMode.SANDBOX)

    store.save(expected)

    assert store.load() == expected
    assert path.stat().st_mode & 0o777 == 0o600
    assert not tuple(path.parent.glob("*.tmp"))


def test_missing_configuration_loads_disabled_live_mode(tmp_path: Path) -> None:
    store = LocalProviderConfigStore(tmp_path / "providers.json")

    configuration = store.load()

    assert configuration.mode is ProviderMode.LIVE
    assert configuration.providers == ()
