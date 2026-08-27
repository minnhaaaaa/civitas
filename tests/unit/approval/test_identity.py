from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from civitas.identity.context import AuthenticatedPrincipal, derive_operator_context


def test_derivation_uses_verified_principal_claims() -> None:
    principal = AuthenticatedPrincipal(
        organization_id="org-a",
        operator_id="operator-a",
        subject="oidc:subject-a",
        authenticated_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
        roles=("procurement-approver",),
    )

    context = derive_operator_context(principal, correlation_id="correlation-a")

    assert context.organization_id == "org-a"
    assert context.operator_id == "operator-a"
    assert context.authentication_subject == "oidc:subject-a"
    assert context.correlation_id == "correlation-a"


def test_principal_rejects_naive_authentication_time() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        AuthenticatedPrincipal(
            organization_id="org-a",
            operator_id="operator-a",
            subject="oidc:subject-a",
            authenticated_at=datetime(2026, 8, 27, 12, 0),
        )
