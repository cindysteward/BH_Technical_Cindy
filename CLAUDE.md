# Pricing exception service

Relationship managers request a discount on a mortgage application's
rate, and reviewers approve or decline it. The mortgage process reads the
approved discount. Python 3.11+, FastAPI, SQLite, plain JavaScript UI.

This file is for coding agents and for people. Always read it before changing
anything.

## Commands

| Command | Purpose |
|---|---|
| `make install` | Create `.venv` and install dependencies |
| `make run` | Start API and UI on port 8000 with auto-reload |
| `make test` | Run the test suite |
| `make lint` | `ruff check`, `ruff format --check` and `mypy --strict` |
| `make check` | Lint, tests and the invariant check. Run before every commit. |
| `make demo` | Run a scenario against a running service |
| `make reset` | Delete the local database |

## Architecture

| Layer | Files | Responsibility |
|---|---|---|
| API | `app/api.py`, `app/schemas.py` | HTTP shapes, status codes, caller lookup |
| Service | `app/service.py` | One use case per transaction, idempotency, storage-backed checks |
| Domain | `app/domain.py`, `app/errors.py` | Business rules as pure functions over immutable values |
| Repository | `app/repository.py` | SQL and the `Store` and `Database` protocols |
| UI | `app/static/` | Plain HTML, CSS and JavaScript over the public API |

Dependencies point inwards. The domain imports nothing from the other
layers, the service never imports `sqlite3`, and SQL lives only in the
repository. The UI may only call the public API.

Read `docs/architecture.md` for data flow and `docs/decisions.md` for why
the design looks like it does.

## Rules that must hold

`docs/invariants.md` lists the guarantees of the service and the tests
that protect them. A feature change does not get to weaken one. If a task
seems to need that, stop and ask.

- A submitted request never changes its discount or reason. A change is a
  new request that links to the old one.
- An approval applies to the exact request the reviewer saw.
- An application has at most one pending or approved request.
- Operations that create data take an idempotency key.
- Nobody decides their own request, and decisions are final.
- Every change writes an event with the actor and time.

## How to make common changes

**New business rule:** Add it to `app/domain.py` as a function that
returns a `Transition`, add unit tests in `tests/test_domain.py`, then
call it from the service. If it is a guarantee other teams rely on, add a
row to `docs/invariants.md` and a `test_invariant_*` test.

**New endpoint:** Add the service method first, with its test. Then add
the schema in `app/schemas.py` and the route in `app/api.py`. Map any new
error class in the API error table. Add an API test for the status codes.

**Schema change:** Edit `SCHEMA` in `app/repository.py`, keep the
partial unique index and the conditional updates, and add a repository
test. Existing databases need a migration, so say so in the pull request.

**New setting:** Add it to `Settings` in `app/config.py` with a default,
read it in `from_env`, and list it in `docs/operations.md`.

**UI change:** Use the API as it is. Hiding a button is a convenience,
so the rule it reflects must stay enforced in the API.

## Code conventions

- Full type hints. `mypy --strict` must pass.
- 79 characters per line. `ruff format` is the formatter.
- Every module starts with a docstring that describes what the module is
  and does, at overview level. State what exists. Do not describe history
  or what is missing.
- Every function has a Google-style docstring with `Args`, `Returns` and
  `Raises` where they apply.
- Explain code through names and docstrings. Avoid inline comments.
- Money-like values are integers (basis points). Never use floats.
- Timestamps are timezone-aware UTC.
- Raise the typed errors in `app/errors.py`. Do not return error codes or
  catch broad exceptions to hide a failure.
- Keep writes inside one `db.write()` block per use case.

## Tests

- Rules are tested without a database in `tests/test_domain.py`.
- Use cases and concurrency are tested against a real SQLite file. Do not
  replace it with a mock, because locking behaviour is part of what is
  being tested.
- Name tests after the behaviour they prove. Tests for registered
  invariants start with `test_invariant_`.
- Never delete, skip, rename or weaken a `test_invariant_*` test to get a
  green build. `scripts/check_invariants.py` fails if you do.
- Fix a bug by writing the failing test first.

## Review

Before opening a pull request, ask the `invariant-reviewer` agent in
`.claude/agents/` to review the diff against `docs/invariants.md` and
`docs/decisions.md`. It reports concrete ways a rule could break.

## Pull requests

- Keep a pull request to one change, with `make check` passing.
- Describe the behaviour change and link the decision in
  `docs/decisions.md` that it relies on or changes.
- When a decision changes, add a new entry in `docs/decisions.md` and
  leave the old one in place.

## Limits to remember

- Caller identity comes from the `X-User-Id` header. Do not build features
  that assume it is trustworthy beyond what the header provides.
- SQLite allows one writer at a time, which caps write throughput. Moving
  to PostgreSQL only changes `app/repository.py`.
- Keys, events and requests are never deleted by the service.
