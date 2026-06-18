# ADR-006: 12-Factor, Environment-Based Configuration

## Status
Accepted

## Context
FieldOpsIQ needs to run identically across local development, a staging
environment used to validate field-deployment images, and production
field-device deployments — without code changes or rebuilds between them.
Configuration includes model choices, file paths, secrets (API tokens), and
operational tunables (rate limits, sync backoff).

## Decision
All configuration is centralized in a single `Settings` class
(`src/config/settings.py`) built on `pydantic-settings`, with every field
overridable via an environment variable of the same name (case-insensitive),
loaded from a `.env` file in development and from real environment variables
(injected by Docker/Kubernetes) in deployed environments.

Rationale:
- This is the standard 12-factor app pattern: strict separation of config
  from code means the same Docker image can be promoted from staging to
  production without modification — only the environment changes.
- A single `Settings` object (rather than scattered `os.environ.get()` calls
  throughout the codebase) gives one place to see every tunable, one place
  to validate them (`field_validator` on `environment` and `whisper_device`),
  and one cached singleton (`get_settings()`, `@lru_cache`) so the same
  validated config object is reused everywhere rather than re-parsed.
- `validate_production_secrets()` is called explicitly at startup (in the
  FastAPI `lifespan` handler) rather than relying on every code path to
  remember to check for a missing token — this fails the container fast and
  loudly in production if `API_AUTH_TOKEN` or `CENTRAL_SERVER_API_KEY` is
  unset, rather than silently running with no auth.
- `.env.example` is committed (with placeholder/empty secret values) so a new
  developer or deployment engineer has a single reference for every
  configurable value without needing to read the `Settings` class source.

## Consequences
- Tests construct `Settings(...)` directly with explicit test values (see
  `tests/conftest.py`'s `test_settings` fixture) rather than relying on
  environment variables, keeping test configuration explicit and isolated
  from whatever `.env` happens to exist on the machine running the tests.
- Adding a new tunable always means: add the field to `Settings`, add it to
  `.env.example`, and (if it's a domain-level threshold rather than a
  deployment concern) consider whether it belongs in `domain/constants.py`
  instead — see ADR-004 for that distinction.
- Because `get_settings()` is `lru_cache`d, changing an environment variable
  after the process has started has no effect without a restart — this is
  intentional (avoids config-drift mid-process) but means dynamic
  reconfiguration is out of scope.
