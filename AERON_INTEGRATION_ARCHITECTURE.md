# Aeron integration architecture

Environmental readings from the Aeron station reach the existing Weather UI through the K-COSMOS main API. Ingestion is a separate worker that calls the Aeron Live3 REST API v1 with a Bearer API key. No browser is involved, and no visitor request ever contacts Aeron. The source contract is in [AERON_REAL_API_CONTRACT.md](AERON_REAL_API_CONTRACT.md).

The previous design logged in with Playwright and headless Chromium to obtain a session cookie. It has been removed: there is no browser login, no `Dockerfile.worker`, and no `playwright` dependency.

```
Aeron Live3 REST API v1 (https://live3.aeronsystems.com/api/v1)
   ▲  GET /timeseries?station={id}   Authorization: Bearer AERON_API_KEY
   │  (every AERON_POLL_INTERVAL_SECONDS, default 300 s)
Aeron worker  (python -m app.environment.worker, single instance, same image as the API)
   │  normalize → validate → INSERT … ON CONFLICT (station_id, observed_at) DO NOTHING
PostgreSQL  environment.readings / environment.ingestion_runs
   │
Main API  GET /api/environment/latest | /history | /status | /parameters   (read-only)
   │  same-origin: Nginx (production) / serve-staging.py :3001 (local staging)
Existing Weather UI  (apps/public-dashboard-final-staging/weather.js — unchanged)
```

## Components

| Piece | Location | Responsibility |
| --- | --- | --- |
| Settings | `services/main-api/app/environment/config.py` | `AeronWorkerSettings`. Read from the process environment only (`env_file=None`); Docker Compose injects the values. The key is a `SecretStr`. HTTPS is enforced; the poll interval is clamped to 120–3600 s. |
| REST client | `…/environment/source.py` | `AeronApiClient`: one pooled `httpx` client, Bearer key, redirects refused so the key never leaves the origin. Retries only timeouts, network errors and 5xx (bounded exponential backoff with jitter). Maps 401/403/404/429 to fixed error codes and parses `Retry-After` / `X-Ratelimit-Reset`. Tracks `X-Ratelimit-Remaining-*`. |
| Parameter map | `…/environment/parameters.py` | The 35 Aeron parameter IDs bound to the existing `environment.readings` columns, with source units. |
| Normalizer | `…/environment/normalizer.py` | Version 2. Parses the `/timeseries` payload to an aware UTC `observed_at`, keeps `epoch_ms`, keeps unknown IDs in `unmapped_values`, flags missing or non-numeric values, rejects a payload for another station (`STATION_MISMATCH`) or with no timestamp or metrics. |
| Freshness | `…/environment/freshness.py` | Server-time classifier: LIVE `< 20 min`, STALE `20–60 min`, OFFLINE beyond that, missing, or more than 2 min in the future. |
| Repository | `…/environment/repository.py` | Idempotent insert, latest reading, bounded history, run records. |
| Worker | `…/environment/worker.py` | Single-threaded loop, `--once`, PostgreSQL advisory lock, SIGTERM-safe, `PollScheduler`. |
| API | `…/routers/environment.py`, `…/schemas/environment.py` | Public read-only routes. |
| Schema | `alembic/versions/0012_environment_readings.py`, `0018_aeron_api_v1.py` | `environment` schema. `0018` adds three nullable columns only. |

## Worker cycle

1. On start, the worker exits with code 3 if `AERON_API_KEY` is empty, and with code 2 if another worker holds the advisory lock.
2. After a restart, the first poll waits until one interval has passed since the last recorded run, so redeploys never add Aeron calls. The worker then logs any drift between Aeron's `/parameters` metadata and the local map (IDs only; never fatal).
3. `GET /timeseries?station={id}`. Normalize and validate, then insert only if `(station_id, observed_at)` is new. A repeated observation leaves the first stored row unchanged.
4. Record one `environment.ingestion_runs` row: outcome `INSERTED` / `DUPLICATE` / `FAILED` and a fixed `error_code`. `login_performed` is always `false` now; it stays `true` on historical rows written by the retired browser-login worker.
5. Choose the next delay:

| Last outcome | Next poll |
| --- | --- |
| Success, or a transient failure (`TIMEOUT`, `NETWORK_ERROR`, `HTTP_ERROR`, `INVALID_READING`, `DATABASE_ERROR`) | After the normal interval |
| `RATE_LIMITED` (429) | After `Retry-After` (at least one interval) |
| `AUTH_FAILED`, `FORBIDDEN`, `NOT_FOUND`, `STATION_MISMATCH` | 5 minutes, doubling to 1 hour, because retrying cannot fix them |
| Fewer than 500 requests left for the day | At most every 30 minutes |

Logs and run rows contain codes, counts and timestamps only, never the key, request headers or upstream bodies.

## Storage — `environment.readings`

Append-only, unique `(station_id, observed_at)`. Columns: `id`, `station_id`, `observed_at` (sensor time, UTC), `ingested_at` (storage time, UTC), `source_recorded_at_raw`, `source`, `source_route` (now `GET /api/v1/timeseries?station={station_id}`), `normalizer_version` (now `2`), `quality_flags`, `raw_payload`, the 35 metrics (double precision), and `network` / `battery` / `charging` / `device_temp`.

Migration `0018_aeron_api_v1` adds, all nullable:

- `fetched_at`: when the worker received the response.
- `source_epoch_ms`: Aeron's `epoch_ms` for the reading.
- `unmapped_values`: parameter IDs not in the local map, with their raw values.

Rows stored before `0018` keep their values and have `NULL` in the new columns. Nothing is dropped, rewritten or backfilled.

## API (main API, port 8000 internally)

| Route | Behaviour |
| --- | --- |
| `GET /api/environment/latest?station_id=` | Newest persisted reading plus server-side `freshness`; 404 if none. Now also includes `fetched_at` (`null` on older rows). |
| `GET /api/environment/history?station_id=&start=&end=&limit=` | Newest first; `limit` 1–1000 (default 100); inverted range → 422. |
| `GET /api/environment/status` | `freshness`, `age_seconds`, `observed_at`, `ingested_at`, thresholds, last sync outcome and error code, last successful sync, DB connectivity. |
| `GET /api/environment/parameters` | New. Static parameter metadata (ID, field, name, unit, category). No upstream call. |

All routes send `Cache-Control: no-store`. There is no POST or sync route.

The Weather UI needs no change: it uses the server's `freshness` field. Its built-in 10/30-minute boundary is only a fallback for a response without `freshness`, which this API never sends.

## Configuration

| Variable | Required | Default |
| --- | --- | --- |
| `DATABASE_URL` | yes | — |
| `AERON_STATION_ID` | yes | — |
| `AERON_API_KEY` | yes (secret) | — |
| `AERON_API_BASE_URL` | no | `https://live3.aeronsystems.com/api/v1` |
| `AERON_POLL_INTERVAL_SECONDS` | no | `300` (clamped to 120–3600) |
| `AERON_CONNECT_TIMEOUT_SECONDS` | no | `5` |
| `AERON_READ_TIMEOUT_SECONDS` | no | `15` |
| `AERON_MAX_ATTEMPTS` | no | `3` |

No longer used: `AERON_USERNAME`, `AERON_PASSWORD`, `AERON_SESSION_COOKIE`, `AERON_DASHBOARD_URL`, `AERON_INTERNAL_BASE_URL`, `AERON_REGION`, `AERON_HTTP_TIMEOUT_SECONDS`, `AERON_LOGIN_TIMEOUT_SECONDS`, `AERON_LOGIN_URL`, `AERON_BASE_URL`.

## Docker

The worker uses the API's `Dockerfile` with `command: ["python", "-m", "app.environment.worker"]`. It publishes no ports and needs no browser, so no extra memory for Chromium.

| File | Worker service |
| --- | --- |
| `compose.dev.yaml` (repository root) | `environment-worker`, behind the opt-in `aeron` profile. Never started by a plain `up`. |
| `services/main-api/compose.worker.yaml` | Add-on to `compose.yaml` for the `microcosm-local` stack. |
| `services/main-api/compose.production.yaml` | `environment-worker`, values from `.env` on the server. |

Run `alembic upgrade head` once per environment before starting the new worker, so `0018` exists.

## Operations

- One cycle by hand: `python -m app.environment.worker --once` (exit 0 on success, 1 on a failed cycle).
- Alert when `/api/environment/status` shows `last_sync_outcome=FAILED` repeatedly, or `freshness=OFFLINE` for longer than an agreed window.
- Nginx routes `/api/` (including `/api/environment/`) to the main API. There is no `/sync` path.
