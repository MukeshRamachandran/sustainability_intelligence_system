# Production readiness

Decision: **not ready to call production healthy.** The repository fixes are tested. They are not deployed, and the PostgreSQL integration suite did not run.

## Startup and configuration

| Check | Status | Evidence |
|---|---|---|
| Production settings reject default secrets, insecure cookies, HTTP public URL, and the development database password | **PASS** | `tests/test_config.py` |
| Production settings reject a relative certificate storage root | **PASS** | `test_production_rejects_relative_certificate_storage` |
| `compose.production.yaml` points the API at the certificate volume | **PASS** in the file. **NOT TESTED** on the server | `CERTIFICATE_STORAGE_ROOT=/app/certificate-storage` added next to the existing mount |
| Running API process uses that path | **NOT TESTED** | Container was not recreated |
| Aeron password removed from the tracked example | **PASS** in the working tree | Rotate the live password; git history still has the old value |

## Database

| Check | Status |
|---|---|
| Connect to production PostgreSQL | **NOT TESTED** (forbidden for this audit) |
| Schema matches Alembic head `0017_public_certificate_registry` | **BLOCKED** |
| Migration upgrade or downgrade | **NOT TESTED** |
| Isolated integration database | **BLOCKED** — no PostgreSQL URL in this environment |

## Health

| Check | Status |
|---|---|
| `/health/live` returns ok | **PASS** via unit test |
| `/health/ready` returns 503 when the database, evidence, or certificate directory is unusable, and does not leak paths | **PASS** via unit test |
| Live production `/health/ready` after this change | **NOT TESTED** |

The Docker healthcheck only checks that the URL opens. It does not inspect certificate files.

## Certificate files

Published 2025 certificate metadata was readable from the public API. Both file URLs returned 404 `Certificate file is unavailable.` That was observed before this fix and has not been rechecked on the server.

Before recreating the API container:

1. Copy any certificate objects out of the running container's `/app/.local/certificates` into `/opt/sustainability-data/certificate-storage`.
2. Deploy the compose change so `CERTIFICATE_STORAGE_ROOT` is `/app/certificate-storage`.
3. Confirm `GET /api/public/certificates/{id}/file` returns the JPEG, not a 404.

This audit did not perform those steps.

## Authentication and security

| Check | Status |
|---|---|
| Unauthenticated access, CSRF, and cross-domain manager tests | **BLOCKED** (PostgreSQL suite skipped) |
| Code review: writes use CSRF; public reads do not require a session | **PASS** as a static review, not a live test |
| SQL built from request strings | No match in routers. **PASS** as a static review |
| Committed Aeron secret in `.env.example` | **FAIL** before the edit. Working tree **PASS**. History and rotation **NOT TESTED** |

## Worker and external systems

The environment worker is a separate process. It was not started. Aeron was not called.

## Nginx

Static review of the public dashboard: API calls are same-origin (`/api/public/...`, `/api/environment/...`). `serve-staging.py` documents production as browser to Nginx to `/api/public/*` to the main API. Nginx configuration was not in this checkout and was not changed. Routing on the live host is **NOT TESTED** beyond the earlier public certificate GET.

## Deployment blockers

1. Apply the certificate storage environment change only after copying existing files onto the volume.
2. Rotate the Aeron password that was in `.env.example`.
3. Run the skipped PostgreSQL suite against a disposable database at Alembic head before treating auth and publication as verified.
4. Re-check `/health/ready` and one published certificate file URL after deploy.

Until those are done, production readiness is **FAIL** for certificate file delivery and **BLOCKED** for database-backed API behavior.
