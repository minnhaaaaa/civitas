"""HTTP and SSE delivery adapters."""

__all__ = [
    "ApprovePlanRequest",
    "ResumeWorkflowRequest",
    "StartWorkflowRequest",
    "WorkflowAPIService",
    "WorkflowStateResponse",
    "WorkflowStore",
    "create_app",
    "create_guarded_app",
]


def __getattr__(name: str) -> object:
    if name == "create_app":
        from civitas.api.app import create_app

        return create_app
    if name in {
        "ApprovePlanRequest",
        "ResumeWorkflowRequest",
        "StartWorkflowRequest",
        "WorkflowAPIService",
        "WorkflowStateResponse",
        "WorkflowStore",
        "create_guarded_app",
    }:
        from civitas.api import guarded_api

        return getattr(guarded_api, name)
    raise AttributeError(name)
