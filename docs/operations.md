# Runbook

How to run the service and fix common problems.

## Configuration

All settings are environment variables with working defaults.

| Variable | Default | Meaning |
|---|---|---|
| `PRICING_DB_PATH` | `data/pricing.db` | SQLite file. The folder is created if missing. |
| `PRICING_MAX_DISCOUNT_BPS` | `100` | Largest discount a request may ask for. |
| `PRICING_SEED_DEMO` | `true` | Create example requests when the database has none. |
| `PORT` | `8000` | Port for `make run`, for example `make run PORT=9000`. |

Users and applications are always seeded into an empty database, because
the service cannot work without them.

## Common tasks

| Task | Command |
|---|---|
| Start with auto-reload | `make run` |
| Start without example requests | `PRICING_SEED_DEMO=false make run` |
| Start from a clean database | `make reset`, then `make run` |
| Run every check CI runs | `make check` |
| Run the demo scenario | `make demo` (service must be running) |
| Run in a container | `make docker` |

## Logs

The service logs one line per state change at `INFO`:

```
request_submitted id=… application=APP-1005 by=arthur
request_revised old=… new=… by=arthur
request_approved id=… by=tricia
request_declined id=… by=fenchurch
request_withdrawn id=… by=ford
```

Errors caused by the caller (4xx) are not logged as errors. A 500 means a
bug and shows a stack trace.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `401 unauthenticated` | Missing `X-User-Id`, or a user that is not seeded |
| `422` mentioning `Idempotency-Key` | Header missing or over 128 characters. Also returned when a key is reused for another payload. |
| `409` on create | The application already has a pending or approved request |
| `409` on decide | Someone else decided or withdrew it first. Reload. |
| `make demo` says there are no free applications | Run `make reset` and restart the service |
| `database is locked` | Another process holds a long write. Stop extra servers using the same file. |

## Safe changes

- New rule: add it in `domain.py` with a test, then add a row to
  `invariants.md` if it must never break.
- New endpoint: add the route in `api.py` and the use case in
  `service.py`. Keep all SQL in `repository.py`.
- Schema change: there is no migration tool yet. The schema uses
  `CREATE TABLE IF NOT EXISTS`, so changing an existing table needs
  `make reset` locally. Would add a migration tool before the first deployment.
- Run `make check` before pushing. CI runs the same steps.
