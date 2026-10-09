# API inventory

Generated from the FastAPI application in `services/main-api` by calling `create_app()` with test settings and reading the OpenAPI document. Production disables `/docs` and `/openapi.json` (`APP_ENV=production`). Those framework routes are not part of the 72 application endpoints below.

`services/report-generation` has no HTTP routes. There is no separate `services/aeron-api` in this checkout. Aeron ingestion is `python -m app.environment.worker`, not an HTTP API.

Alembic head: `0017_public_certificate_registry` (single head, 18 version files). `alembic current` was not run because no PostgreSQL URL was configured.

## Authentication model

| Marker | Meaning |
|---|---|
| Public | No session. |
| Session | Cookie `microcosm_session`. Dependency `AuthenticatedUser`. |
| Admin | Session, role `ADMIN`, password already changed. Dependency `AdminUser`. |
| Manager | Session, role `MANAGER` with a domain, password already changed. Dependency `ManagerUser`. |
| CSRF | Session plus matching `X-CSRF-Token` header and `microcosm_csrf` cookie. Dependency `CsrfUser`. Writes also check admin or manager role inside the handler. |

Database access is a SQLAlchemy session (`DbSession`) unless noted. Unhandled exceptions become HTTP 500 `{"error":{"code":"internal_error",...}}` and do not return the traceback.

## Health

| Method | Path | Auth | Source | Behavior |
|---|---|---|---|---|
| GET | `/health/live` | Public | `app/routers/health.py` | `{"status":"ok"}`. |
| GET | `/health/ready` | Public | `app/routers/health.py` | 200 when database, evidence directories, and certificate storage are writable. 503 otherwise. Body fields: `status`, `database`, `evidence`, `certificates`. Does not echo paths or secrets. |

## Authentication — `app/routers/auth.py`

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/auth/login` | Public | Body `LoginRequest`. Sets session and CSRF cookies. Lockout after repeated failures. |
| GET | `/api/auth/session` | Session | Returns the current user. |
| POST | `/api/auth/logout` | CSRF | Revokes the session and clears cookies. |
| POST | `/api/auth/change-password` | CSRF | Body with the new password. |

## Access probes — `app/routers/access.py`

| Method | Path | Auth |
|---|---|---|
| GET | `/api/access/manager/{domain}` | Manager, and the domain must match the assignment |
| GET | `/api/access/admin` | Admin |

## Public dashboard — `app/routers/releases.py`

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/public/dashboard` | Public | Optional `year`, `month`. Active published release. |
| GET | `/api/public/dashboard/timeline` | Public | Period selector used by the public dashboard. |
| GET | `/api/public/dashboard/history` | Public | Published history. |

Frontend callers: `apps/public-dashboard-final-staging/public-api.js`, `public-data-loader.js`.

## Public certificates — `app/routers/certificates.py`

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/public/certificates/years` | Public | Query `domain`. Published years only. |
| GET | `/api/public/certificates` | Public | Query `domain`, `year` (2000–2100). Published rows only. |
| GET | `/api/public/certificates/{certificate_id}/file` | Public | Published file only. Query `download`. 404 for unknown, draft, archived, or a missing stored file (`Certificate file is unavailable.`). |

Storage key must match `^[0-9a-f]{32}\.(pdf|png|jpg)$` and stay inside `CERTIFICATE_STORAGE_ROOT`.

## Environment — `app/routers/environment.py`

Reads persisted Aeron rows only. Does not call Aeron.

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/environment/latest` | Public | Optional `station_id`. 404 when empty. `Cache-Control: no-store`. |
| GET | `/api/environment/history` | Public | `station_id`, `start`, `end`, `limit` 1–1000. 422 when `start` is after `end`. |
| GET | `/api/environment/status` | Public | Freshness and last worker run. Database errors become `connection_status: error`, not a traceback. |

Frontend caller: `apps/public-dashboard-final-staging/weather.js` (`API_BASE = '/api/environment'`).

## Manager outreach — `app/routers/manager_outreach.py`

Prefix `/api/manager`. Reads use Manager. Writes use CSRF and require the outreach domain.

| Method | Path |
|---|---|
| GET | `/api/manager/periods` |
| GET | `/api/manager/outreach/current-period` |
| GET | `/api/manager/outreach/programmes` |
| POST | `/api/manager/outreach/programmes` |
| GET | `/api/manager/outreach/programmes/{programme_id}` |
| PUT | `/api/manager/outreach/programmes/{programme_id}` |
| DELETE | `/api/manager/outreach/programmes/{programme_id}` |
| GET | `/api/manager/outreach/submissions` |
| POST | `/api/manager/outreach/submissions/{submission_id}/submit` |

## Manager submissions — `app/routers/manager_submissions.py`

`{domain}` is an operational domain. A manager can only use their assigned domain.

| Method | Path | Auth |
|---|---|---|
| GET | `/api/manager/{domain}/periods` | Manager |
| GET | `/api/manager/{domain}/current-period` | Manager |
| GET | `/api/manager/{domain}/metrics` | Manager |
| GET | `/api/manager/waste/catalog` | Manager (waste) |
| GET | `/api/manager/{domain}/submissions/current` | Manager |
| GET | `/api/manager/{domain}/submissions` | Manager |
| GET | `/api/manager/{domain}/submissions/{submission_id}` | Manager |
| POST | `/api/manager/{domain}/submissions` | CSRF + domain |
| PUT | `/api/manager/{domain}/submissions/{submission_id}` | CSRF + domain |
| POST | `/api/manager/{domain}/submissions/{submission_id}/submit` | CSRF + domain |

## Manager evidence — `app/routers/evidence.py`

| Method | Path | Auth |
|---|---|---|
| POST | `/api/manager/{domain}/submissions/{submission_id}/evidence` | CSRF + domain. Multipart PDF/PNG/JPEG, max 10 MB. |
| GET | `/api/manager/{domain}/submissions/{submission_id}/evidence` | Manager |
| GET | `/api/manager/{domain}/evidence/{evidence_id}/content` | Manager. Query `download`. |
| DELETE | `/api/manager/{domain}/submissions/{submission_id}/evidence/{evidence_id}` | CSRF + domain |

## Admin review — `app/routers/admin_outreach.py`

| Method | Path | Auth |
|---|---|---|
| GET | `/api/admin/periods` | Admin |
| GET | `/api/admin/review-queue` | Admin. Query `domain` (default outreach), `reporting_period_id`. |
| GET | `/api/admin/submissions/{submission_id}` | Admin |
| POST | `/api/admin/submissions/{submission_id}/begin-review` | CSRF + admin |
| POST | `/api/admin/submissions/{submission_id}/request-correction` | CSRF + admin. Body includes a reason. |
| POST | `/api/admin/submissions/{submission_id}/approve` | CSRF + admin |

## Admin evidence — `app/routers/evidence.py`

| Method | Path | Auth |
|---|---|---|
| GET | `/api/admin/submissions/{submission_id}/evidence` | Admin. Committed evidence only. |
| GET | `/api/admin/evidence` | Admin. Filters and pagination (`page`, `page_size` 1–100). |
| GET | `/api/admin/evidence/{evidence_id}/content` | Admin. Uncommitted evidence is 404. |

## Admin users — `app/routers/admin_users.py`

| Method | Path | Auth |
|---|---|---|
| GET | `/api/admin/users` | Admin |
| POST | `/api/admin/users` | CSRF + admin |
| POST | `/api/admin/users/{user_id}/reset-password` | CSRF + admin |
| POST | `/api/admin/users/{user_id}/deactivate` | CSRF + admin |
| POST | `/api/admin/users/{user_id}/activate` | CSRF + admin |
| POST | `/api/admin/users/{user_id}/revoke-sessions` | CSRF + admin |

## Audit — `app/routers/audit.py`

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/admin/audit-logs` | Admin | Paginated. Metadata is scrubbed before return. |

## Emission factors — `app/routers/emission_factors.py`

| Method | Path | Auth |
|---|---|---|
| GET | `/api/admin/emission-factor-sets` | Admin |
| POST | `/api/admin/emission-factor-sets` | CSRF + admin |
| GET | `/api/admin/emission-factor-sets/{set_id}` | Admin |
| PUT | `/api/admin/emission-factor-sets/{set_id}` | CSRF + admin |
| POST | `/api/admin/emission-factor-sets/{set_id}/activate` | CSRF + admin |

## Releases — `app/routers/releases.py`

| Method | Path | Auth |
|---|---|---|
| GET | `/api/admin/reporting-periods/{reporting_period_id}/publication-readiness` | Admin |
| POST | `/api/admin/releases/prepare` | CSRF + admin. 201, or not-ready detail. |
| GET | `/api/admin/releases` | Admin. Query `reporting_period_id`. |
| GET | `/api/admin/releases/{release_id}/preview` | Admin |
| POST | `/api/admin/releases/{release_id}/publish` | CSRF + admin |

## Admin certificates — `app/routers/certificates.py`

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/admin/certificates` | Admin | Includes drafts. |
| POST | `/api/admin/certificates` | CSRF + admin | Multipart upload. Creates `DRAFT`. |
| PATCH | `/api/admin/certificates/{certificate_id}` | CSRF + admin | Metadata only. |
| POST | `/api/admin/certificates/{certificate_id}/publish` | CSRF + admin | 409 if the stored file is missing. |
| POST | `/api/admin/certificates/{certificate_id}/archive` | CSRF + admin | |
| GET | `/api/admin/certificates/{certificate_id}/file` | Admin | Any status. `Cache-Control: private, no-store`. |

## Test coverage map

Unit tests that do not need PostgreSQL ran (see `TEST_RESULTS.md`). Auth, submission, evidence, publication, certificate, and historical integration tests exist under `services/main-api/tests/` and were skipped because no PostgreSQL test database was configured. They were not executed.
