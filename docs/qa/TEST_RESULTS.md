# Test results

Date: 2026-10-09. Host Python is 3.14.8. The project declares `requires-python >=3.12,<3.14`, and the production image is Python 3.12.11. A 3.12 interpreter was not installed, so dependencies were installed into `/tmp/kcosmos-qa-venv` without installing the package metadata, and tests ran with `PYTHONPATH=services/main-api`.

PostgreSQL was not available. No `TEST_DATABASE_URL` was set. Production was not used as the test database.

## Commands

```bash
cd services/main-api
PYTHONPATH=. /tmp/kcosmos-qa-venv/bin/pytest -q --tb=line
/tmp/kcosmos-qa-venv/bin/ruff check app tests
PYTHONPATH=. /tmp/kcosmos-qa-venv/bin/alembic heads
```

## Pytest

| Result | Count |
|---|---|
| Passed | 122 |
| Failed | 0 |
| Skipped | 202 |
| Errors | 0 |

One Starlette deprecation warning: `httpx` with `starlette.testclient` is deprecated in favor of `httpx2`. It did not fail a test.

There was no separate pre-fix pytest run. The environment had no pytest until this audit, and the certificate-storage and readiness changes were included in this run. The new and updated tests in that run passed:

- `test_production_rejects_relative_certificate_storage`
- `test_production_accepts_replaced_security_values`
- `test_ready_succeeds_when_dependencies_are_ready`
- `test_ready_fails_safely_when_database_is_unavailable`
- `test_ready_fails_safely_when_evidence_root_is_missing`
- `test_ready_fails_when_certificate_storage_is_missing`

## What the 122 passing tests cover

Config validation, health, a SQLite-backed app boot, environment normalization and freshness, parts of the public calculation and publication contracts that do not open PostgreSQL, and other unit tests that do not request `postgres_engine`.

## What was skipped

Every test that depends on `postgres_engine` skipped with `PostgreSQL integration database is not configured`. That includes:

- `test_auth_integration.py`
- `test_admin_security_integration.py`
- `test_certificates.py` (the PostgreSQL cases)
- `test_evidence_integration.py`
- `test_evidence_repository_integration.py`
- `test_generic_submission_integration.py`
- `test_generic_submission_domain_matrix.py`
- `test_outreach_integration.py`
- `test_publication_integration.py`
- `test_publication_readiness_integration.py`
- `test_historical_integration.py`
- `test_environment_postgres_integration.py`
- `test_current_month_security.py`
- waste, water, DG, LPG, emission-factor, and zero-semantics integration cases

Those results are **BLOCKED**, not **PASS**.

## Other checks

| Check | Status |
|---|---|
| `ruff check app tests` | **PASS** |
| `alembic heads` | **PASS** — single head `0017_public_certificate_registry` |
| `alembic current` | **BLOCKED** — no database |
| `alembic upgrade` / downgrade | **NOT TESTED** — would change a database |
| Live HTTP calls against production after the fix | **NOT TESTED** |
| Worker against Aeron | **NOT TESTED** — real credentials must not be used |
| Frontend contract unit tests (`node --test`) | **NOT TESTED** in this audit. Route paths were compared statically and match. |

## Endpoint coverage

72 application routes were inventoried from OpenAPI. Unit tests exercise health, config, and some public-contract behavior. Authenticated route behavior is covered by the skipped PostgreSQL suite and was not executed here.
