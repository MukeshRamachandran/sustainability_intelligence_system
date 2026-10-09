# QA findings

Statuses: **PASS** executed and passed, **FAIL** executed and failed, **BLOCKED** could not run, **NOT TESTED** not executed.

No production database, container, or Nginx change was made.

## QA-001 — Production certificate files are not read from the mounted volume

- Severity: **High**
- Component: `GET /api/public/certificates/{certificate_id}/file`, `services/main-api/compose.production.yaml`, `app/core/config.py`
- Status of the code fix: **PASS** (unit tests). Status on the live server: **NOT TESTED** after the fix, because the running container was not changed.

### Reproduction

1. `compose.production.yaml` mounts `/opt/sustainability-data/certificate-storage` at `/app/certificate-storage`.
2. The API service did not set `CERTIFICATE_STORAGE_ROOT`.
3. The setting defaults to the relative path `.local/certificates`. The image `WORKDIR` is `/app`, so the process uses `/app/.local/certificates`, which is not the mount.
4. On 2026-10-04, `GET https://sustainability.kct.ac.in/api/public/certificates?domain=waste&year=2025` returned two published certificates, and `GET /api/public/certificates/{id}/file` for both returned HTTP 404 `Certificate file is unavailable.`

### Expected

The API reads and writes the directory that the production volume mounts.

### Actual

Metadata is in PostgreSQL. The file lookup uses a different directory, so a published certificate whose bytes are on the volume (or were written only inside the container filesystem) returns 404.

### Root cause

`compose.yaml` sets `CERTIFICATE_STORAGE_ROOT=/app/certificate-storage`. `compose.production.yaml` mounted that path and never set the variable. Production settings also accepted the relative default, unlike `EVIDENCE_ROOT`, which must be absolute.

### Fix

- `compose.production.yaml` now sets `CERTIFICATE_STORAGE_ROOT=/app/certificate-storage` on the API service.
- Production settings reject a relative `CERTIFICATE_STORAGE_ROOT`.
- `.env.example` documents the variable.

### Regression test

`tests/test_config.py::test_production_rejects_relative_certificate_storage`  
`tests/test_config.py::test_production_accepts_replaced_security_values` (absolute certificate root)

### Verification

Both tests passed in the 2026-10-09 pytest run.

### Remaining risk

The running production container was not recreated. Files already written under `/app/.local/certificates` inside that container are not on the volume. Copy them to `/opt/sustainability-data/certificate-storage` before recreating the container, or the 404 remains. This audit did not copy those files.

## QA-002 — Aeron credentials were committed in `.env.example`

- Severity: **Critical**
- Component: `services/main-api/.env.example` (tracked by git). `.env` itself is gitignored.
- Status: **PASS** for the working tree. Git history was not rewritten.

### Reproduction

`git ls-files` includes `services/main-api/.env.example`. The file contained an Aeron username and password under the worker settings. The values are not repeated here.

### Expected

Example files contain placeholders. Real Aeron credentials stay in a secret store.

### Actual

A live-looking username and password were in the tracked example.

### Root cause

The example file was filled with operational values instead of placeholders.

### Fix

Those two fields were replaced with `replace-with-aeron-username` and `replace-with-aeron-password`. No login to Aeron was attempted.

### Regression test

None. This is a secret-removal change, not an API behavior change.

### Verification

A search for `AERON_PASSWORD=` now matches only the placeholder line in `.env.example`.

### Remaining risk

The previous value is still in git history. Rotate the Aeron password. Do not assume the placeholder is what production uses; production `.env` was not read.

## QA-003 — Readiness ignored certificate storage

- Severity: **Medium**
- Component: `GET /health/ready`
- Status: **PASS**

### Reproduction

`/health/ready` reported `database` and `evidence` only. Certificate storage could be missing while the process was ready, which matches a service that lists certificates and cannot serve their files.

### Expected

Readiness fails when the certificate storage directory is missing or not writable, without revealing the path.

### Actual

Only the database and evidence directories were checked.

### Root cause

`app/routers/health.py` never inspected `CERTIFICATE_STORAGE_ROOT`.

### Fix

The response now includes `certificates` (`ready` or `unavailable`). The route returns 503 if that directory is not usable. The field is additive. The Docker healthcheck only requires a successful HTTP response.

### Regression test

`tests/test_health.py::test_ready_fails_when_certificate_storage_is_missing`  
Existing ready/database/evidence assertions updated for the new field.

### Verification

Those tests passed in the 2026-10-09 pytest run.

### Remaining risk

A writable directory with missing certificate bytes still returns ready. QA-001 is the file-location defect. Readiness cannot see individual missing objects.

## QA-004 — PostgreSQL integration suite was not executed

- Severity: **Medium** (coverage gap, not a confirmed product defect)
- Component: auth, admin security, certificates, evidence, submissions, publication, historical import, environment persistence
- Status: **BLOCKED**

### Reproduction

`pytest` skipped 202 tests with `PostgreSQL integration database is not configured`. `TEST_DATABASE_URL` and `DATABASE_URL` were not a PostgreSQL URL in this environment. A disposable database was not started, because this checkout has no local PostgreSQL server and production must not be used.

### Expected

Those tests run against an isolated database migrated to head `0017_public_certificate_registry`.

### Actual

Skipped. Not failed.

### Fix

None. Running them against production was refused.

### Remaining risk

Authorization, CSRF, upload, and publication behavior are implemented and have tests, but this audit did not execute those tests. Do not treat them as passed.

## Checked and not raised as defects

- Public, manager, and admin paths used by `apps/public-dashboard-final-staging` and `apps/manager-admin` match the 72 routes above. No localhost API base is hardcoded in the current public dashboard clients.
- State-changing admin and manager handlers take `CsrfUser` or an admin/manager dependency. Public certificate and environment reads are intentionally unauthenticated.
- Certificate and evidence paths are generated keys, not the uploaded filename. A storage key that escapes the root is rejected.
- Router code does not build SQL with string concatenation. Queries use SQLAlchemy.
- `ruff check app tests` passed.
