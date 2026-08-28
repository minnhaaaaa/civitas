"""Explicit provider-runtime bootstrap for deployed server and worker processes."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import ModuleType
from typing import TYPE_CHECKING, cast

from civitas.runtime.config import RuntimeSettings, SettingsError

if TYPE_CHECKING:
    from civitas.runtime.composition import ProviderExecutionRuntime, ProviderPlanningRuntime


@dataclass(frozen=True, slots=True)
class ProviderRuntimeDependencies:
    planning: ProviderPlanningRuntime
    execution: ProviderExecutionRuntime


ProviderRuntimeFactory = Callable[
    [RuntimeSettings], ProviderRuntimeDependencies | Awaitable[ProviderRuntimeDependencies]
]


async def load_provider_runtime(
    settings: RuntimeSettings,
) -> ProviderRuntimeDependencies | None:
    path = settings.provider_factory
    if path is None:
        if settings.live_provider_required:
            raise SettingsError("live provider dependencies are required")
        return None
    factory = _load_factory(path)
    result = factory(settings)
    dependencies = await result if inspect.isawaitable(result) else result
    if not isinstance(dependencies, ProviderRuntimeDependencies):
        raise SettingsError("provider factory must return ProviderRuntimeDependencies")
    return dependencies


def _load_factory(path: str) -> ProviderRuntimeFactory:
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name or not attribute:
        raise SettingsError("provider factory must use module:callable syntax")
    try:
        module: ModuleType = importlib.import_module(module_name)
    except ImportError as error:
        raise SettingsError("provider factory module could not be imported") from error
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise SettingsError("provider factory attribute is not callable")
    return cast("ProviderRuntimeFactory", factory)
