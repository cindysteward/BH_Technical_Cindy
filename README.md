# Pricing exception service

A small internal service where relationship managers ask for a discount on
a mortgage rate. reviewers approve or decline it. The mortgage
application process reads the approved discount.

![How the service fits together](docs/images/overview.svg)


## Start

```bash
make install   # creates .venv and installs dependencies
make run       # starts the API and UI on http://localhost:8000
```

Open <http://localhost:8000>. Use the **Signed in as** picker to switch
between people. The database starts with example requests in every state.

In a second terminal:

```bash
make demo      # runs a full scenario against the running service
make check     # lint, types, tests and the invariant check
```

Interactive API docs are at <http://localhost:8000/docs>.

Without `make`:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:create_app --factory --port 8000
pytest
```

![Reviewer view](docs/images/ui-review.png)

## What it does

| Who | Can do |
|---|---|
| Relationship manager | Submit a request, then change or withdraw it while pending |
| Reviewer | Approve or decline someone else's pending request |
| Mortgage process | Read the approved discount of an application (the only role that can) |
| Everyone | See any request with its full history |

Example users: `arthur` and `ford` are relationship managers, `tricia` and
`fenchurch` are reviewers. `cindy` has both roles (shows nobody can
approve their own request). `mortgage-engine` stands for the downstream
system.

## The key decision

"One stakeholder wants managers to change a discount after submitting.
Another wants every approval to apply to exactly what was reviewed."

Both are possible if a request never changes once submitted. Changing the
discount withdraws the current request and creates a new one that links
back to it. The reviewer always decides on a fixed version, but the
manager also still get to change their mind. The history then also shows every version.
See [D1 in decisions](docs/decisions.md#d1-submitted-requests-never-change).

## Assumptions

- Applications and users already exist elsewhere. They are seeded here.
- Authentication I left out of scope. The `X-User-Id` header says who is
  calling, and roles come from the seeded users.
- Discounts are whole basis points between 1 and 100 (configurable), and
  must stay below the standard rate.
- An application has at most one pending or approved request at a time.
- A decision is final. Declining needs a note.

## Deliberately left out of scope

- Real authentication and authorisation.
- Limiting a relationship manager to their own applications.
- Revoking an approval or expiring it after a period.
- Pagination on list endpoints.
- Notifications to reviewers.

## API

All endpoints are under `/api` and need an `X-User-Id` header, apart
from `/api/health` and `/api/users`.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/requests` | Submit a request. Needs `Idempotency-Key`. |
| `POST` | `/requests/{id}/revisions` | Replace a pending request with a new discount. Needs `Idempotency-Key`. |
| `POST` | `/requests/{id}/decision` | Approve or decline. |
| `POST` | `/requests/{id}/withdraw` | Withdraw a pending request. |
| `GET` | `/requests` | List requests. Filter with `status` and `application_id`. |
| `GET` | `/requests/{id}` | One request. |
| `GET` | `/requests/{id}/history` | A request, its earlier versions and all events. |
| `GET` | `/applications` | Applications and their open request. |
| `GET` | `/applications/{id}/approved-discount` | What the mortgage process needs. System role only. |

A retried create with the same key returns `200` with the original request
and an `Idempotent-Replayed: true` header. Reusing a key for a different
payload returns `422`. Errors always look like
`{"error": {"code": "...", "message": "..."}}`.

## Tests

60 tests across four levels. All run in about two seconds.

| File | What it proves |
|---|---|
| `tests/test_domain.py` | Business rules, with no database |
| `tests/test_service.py` | Use cases on real SQLite, including races between parallel calls |
| `tests/test_repository.py` | Database constraints that hold even if the service has a bug |
| `tests/test_api.py` | Full workflows over HTTP, status codes and error shapes |

The rules that must never break are listed in
[docs/invariants.md](docs/invariants.md). Each also with the test that asserts
it. `make check` fails when one of those tests goes missing.

## Repository layout

```
app/
  domain.py      business rules, no I/O
  service.py     use cases, one transaction each
  repository.py  SQLite storage behind a small interface
  api.py         HTTP routes and error mapping
  schemas.py     request and response models
  seed.py        example data for local runs
  static/        the UI, plain HTML and JavaScript
tests/           unit, service, storage and API tests
scripts/         demo scenario and invariant check
docs/            requirements, user guide, design and runbooks
```

## Documentation

| Document | Kind | Read it for |
|---|---|---|
| [requirements.md](docs/requirements.md) | User stories and use cases | What the service must do, each criterion linked to a test |
| [user-guide.md](docs/user-guide.md) | End-user manual | How RMs and reviewers use the screen |
| [architecture.md](docs/architecture.md) | Architecture and technical design | Layers and data model, with diagrams of the main flows |
| [decisions.md](docs/decisions.md) | Architecture decision records | Why things are the way they are |
| [invariants.md](docs/invariants.md) | Business rules register | Rules that must never break, each with its test |
| [operations.md](docs/operations.md) | Runbook | Running and troubleshooting the service |
| [ai-usage.md](docs/ai-usage.md) | AI usage | How agents were used, and the agent tooling (bonus) |
