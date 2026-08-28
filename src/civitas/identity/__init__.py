"""Transport-neutral authentication, authorization, and audit identity."""

from civitas.identity.authentication import (
    BearerCredential,
    BearerVerifier,
    HashedBearerVerifier,
)
from civitas.identity.authorization import ROLE_PERMISSIONS, RoleAuthorizer
from civitas.identity.context import AuthenticatedPrincipal, derive_operator_context
from civitas.identity.rate_limit import FixedWindowRateLimiter, RateLimitDecision, RateLimiter

__all__ = [
    "ROLE_PERMISSIONS",
    "AuthenticatedPrincipal",
    "BearerCredential",
    "BearerVerifier",
    "FixedWindowRateLimiter",
    "HashedBearerVerifier",
    "RateLimitDecision",
    "RateLimiter",
    "RoleAuthorizer",
    "derive_operator_context",
]
