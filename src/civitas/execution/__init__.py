"""Guarded, auditable execution services."""

__all__ = [
    "ConcurrentExecutionRefresher",
    "FreshnessRevalidationError",
    "GuardedExecutionOutcome",
    "GuardedExecutionService",
    "GuardedExecutionServiceV2",
    "RefreshBundle",
    "RefreshInputsPort",
    "RevalidationSnapshot",
]


def __getattr__(name: str) -> object:
    if name in {"FreshnessRevalidationError", "GuardedExecutionService", "RevalidationSnapshot"}:
        from civitas.execution import service

        return getattr(service, name)
    if name in {
        "ConcurrentExecutionRefresher",
        "GuardedExecutionOutcome",
        "GuardedExecutionServiceV2",
        "RefreshBundle",
        "RefreshInputsPort",
    }:
        from civitas.execution import guarded

        return getattr(guarded, name)
    raise AttributeError(name)
