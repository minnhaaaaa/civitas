"""Deterministic role policy for intent-level product operations."""

from __future__ import annotations

from collections.abc import Mapping

from civitas.ports.identity import OperatorContext

ROLE_PERMISSIONS: Mapping[str, frozenset[str]] = {
    # The operator role may drive the complete challenge-bound sequence.  It does
    # not bypass approval policy: execution still requires the immutable receipt.
    "procurement-operator": frozenset(
        {
            "plan_procurement_goal",
            "get_planning_run",
            "get_decision_summary",
            "prepare_execution",
            "approve_execution",
            "execute_approved_plan",
            "get_execution_audit",
        }
    ),
    "procurement-viewer": frozenset(
        {"get_planning_run", "get_decision_summary", "get_execution_audit"}
    ),
    "procurement-planner": frozenset(
        {
            "plan_procurement_goal",
            "get_planning_run",
            "get_decision_summary",
            "prepare_execution",
            "get_execution_audit",
        }
    ),
    "procurement-approver": frozenset(
        {
            "get_planning_run",
            "get_decision_summary",
            "prepare_execution",
            "approve_execution",
            "get_execution_audit",
        }
    ),
    "procurement-executor": frozenset(
        {
            "get_planning_run",
            "get_decision_summary",
            "execute_approved_plan",
            "get_execution_audit",
        }
    ),
}


class RoleAuthorizer:
    """Fail closed for unknown roles, tools, and missing role assignments."""

    def __init__(self, permissions: Mapping[str, frozenset[str]] = ROLE_PERMISSIONS) -> None:
        self._permissions = dict(permissions)

    def permits(self, context: OperatorContext, operation: str) -> bool:
        return any(
            operation in self._permissions.get(role.strip().lower(), frozenset())
            for role in context.roles
        )
