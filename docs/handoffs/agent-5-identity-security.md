# Agent 5 handoff — `live/identity-security`

## Delivered

- Opaque bearer verification using only SHA-256 credential digests, constant-time
  comparison, rotation-friendly multiple records, activation time, expiry, and
  revocation.
- Organization/operator/subject/role derivation into the existing
  `OperatorContext`; request payloads cannot select identity.
- Authenticated Streamable HTTP middleware with validated/generated correlation
  IDs, `WWW-Authenticate`, per-principal rate limiting, and authentication audit
  events that never contain bearer values.
- Deterministic intent-tool role policy. The `procurement-operator` role can drive
  the challenge-bound product sequence but does not bypass approval receipts or
  guarded execution.
- Tenant-scoped repository factory for direct operational records and planning-run
  child records. Cross-tenant reads receive an organization predicate and
  cross-tenant inserts fail before session mutation.
- Adversarial tests for expired/revoked/unknown credentials, role escalation,
  correlation-header injection, throttling, audit identity, and tenant isolation.

## Integration contract for Agent 1

At the production composition root:

1. Load one or more `BearerCredential` records from a secret manager/configuration.
2. Construct `HashedBearerVerifier`, `RoleAuthorizer`, and a `RateLimiter` (a shared
   atomic implementation is recommended for multiple replicas).
3. Construct `InboundMCPServer(..., authorizer=RoleAuthorizer())`.
4. Call `streamable_http_app(verifier=..., rate_limiter=..., audit_sink=...)`.
5. Use `uow.for_organization(context.organization_id)` in authenticated repository
   paths. Unscoped repositories remain only for trusted migration/import jobs.

The current runtime-composition workstream appeared concurrently in the shared
worktree and still calls the legacy resolver callback. It remains source-compatible,
but the integration branch should apply the five steps above to activate role and
rate controls.

## Validation

```text
ruff src + Agent 5 tests: passed
mypy src/civitas: passed (87 source files)
pytest tests/unit tests/security tests/contract: 93 passed
```
