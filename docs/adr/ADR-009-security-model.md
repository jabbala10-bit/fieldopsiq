# ADR-009: Security Model — Localhost-Scoped API, Lightweight Auth, Compliance Mapping

## Status
Accepted

## Context
FieldOpsIQ's API and UI are designed to run **on the same field device**,
typically bound to `localhost`/`0.0.0.0` on a private interface, with the
Gradio UI as the only client talking to the FastAPI backend. This is a
materially different threat model from a multi-tenant SaaS API, and the
security controls should match that reality rather than over-engineering for
threats that don't apply, while still meeting the access-logging and secrets
hygiene expected of field-deployed software handling site/safety data.

## Decision
- **Bearer-token auth**, enforced only outside `development` environment
  (`require_api_token` in `api/dependencies.py`). In development, auth is
  skipped to ease local iteration; in staging/production, a missing or
  mismatched `Authorization: Bearer <token>` header returns `401`.
- **No Redis-backed distributed rate limiting.** `RateLimitMiddleware` is a
  simple in-memory per-IP sliding window, because this is a single-instance
  process — there is no second node to coordinate limits with.
- **Audit logging without request bodies.** `AuditLoggingMiddleware` logs
  method/path/status/latency/client-IP/request-ID for every request, but
  never the request body — transcripts and report content can contain PII
  or sensitive site/safety details, and audit logs are a poor place to also
  store the sensitive payload they're auditing access to.
- **Production secrets fail fast.** `Settings.validate_production_secrets()`
  is called at FastAPI startup and raises `ConfigurationError` if
  `API_AUTH_TOKEN` or (when sync is enabled) `CENTRAL_SERVER_API_KEY` is
  unset in a production environment — the container refuses to start rather
  than silently running unauthenticated.
- **Compliance mapping** (informational, not a certification claim):
  - *ISO 27001 / SOC 2* — addressed via the audit-logging middleware
    (access trail) and the secrets-validation-at-startup pattern (control
    over credential management).
  - *GDPR* — relevant because transcripts may contain technician voice data
    and incidentally-captured personal information; addressed by keeping all
    processing local/offline by default (no third-party LLM API call ever
    happens) and by never persisting raw audio beyond what's needed for the
    pipeline run (the `audio_inbox` directory is local storage the
    deploying organization controls retention policy for).
  - *OWASP API Security Top 10* — addressed at the level appropriate for
    this threat model: auth (API1/API2), rate limiting (API4), and avoiding
    sensitive data in logs (API3) are explicitly handled; broader concerns
    like object-level authorization (API1) are less relevant since this is a
    single-tenant, single-technician-per-device deployment, not a
    multi-tenant API.
  - *IEC 62443* (industrial automation/control systems security) — relevant
    given the utilities/oil-and-gas/manufacturing target customers; the
    offline-first design (ADR-007) and local-only LLM (ADR-002) align with
    62443's preference for minimizing external network dependencies in
    operational environments.

## Consequences
- This security model assumes the **physical device itself** is the trust
  boundary — if an attacker has shell access to the field tablet, this
  application-layer auth doesn't protect against that; device-level security
  (disk encryption, OS user permissions) is out of scope for FieldOpsIQ and
  is the deploying organization's responsibility.
- The bearer token is a single shared secret per device/deployment, not
  per-user — appropriate for a single-technician device, but would need a
  real identity provider (OAuth2/OIDC) if a future deployment puts multiple
  technicians' credentials on one shared device.
- `CENTRAL_SERVER_API_KEY` (used by `SyncQueueWorker` when calling out to the
  central server) is the actual internet-facing credential in this
  architecture — the local `API_AUTH_TOKEN` protects the local API surface,
  while the central server's own auth (out of scope for this case study)
  protects the sync endpoint itself.
