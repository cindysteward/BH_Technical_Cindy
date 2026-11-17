# Architecture and technical design

## Layers

```mermaid
flowchart LR
    UI[UI<br/>app/static] --> API
    Client[Upstream systems] --> API
    API[api.py<br/>routes, headers, errors] --> Service
    Service[service.py<br/>use cases, transactions] --> Domain[domain.py<br/>rules, no I/O]
    Service --> Store[repository.py<br/>Store protocol]
    Store --> DB[(SQLite)]
```

Dependencies point one way. `domain.py` imports nothing from the rest of
the app, so every rule can be tested without a database.

| Module | Owns | Does not do |
|---|---|---|
| `domain.py` | States, transitions, validation, who may do what | Storage or HTTP |
| `service.py` | One transaction per use case, idempotency, cross-record checks, logging | SQL or status codes |
| `repository.py` | SQL, schema, row mapping, constraints | Business decisions |
| `api.py` + `schemas.py` | Routes, headers, JSON shapes, error mapping | Business rules |

Domain functions return a `Transition`: the new request plus the event
that records the change. The service saves both together, so
state and history can't disagree.

## Request lifecycle

```mermaid
stateDiagram-v2
    [*] --> pending: submit
    pending --> approved: reviewer approves
    pending --> declined: reviewer declines (note required)
    pending --> withdrawn: owner withdraws
    pending --> withdrawn: owner revises
    withdrawn --> [*]
    approved --> [*]
    declined --> [*]
    note right of withdrawn
        A revision also creates a new
        pending request with
        supersedes_id = old id
    end note
```

`pending` and `approved` count as open. Only one open request per
application is allowed.

## Retry after a timeout

```mermaid
sequenceDiagram
    participant C as Upstream system
    participant S as Service
    participant D as SQLite
    C->>S: POST /requests (Idempotency-Key: k1)
    S->>D: BEGIN IMMEDIATE
    S->>D: key k1 for this user? no
    S->>D: insert request, event, key k1
    S->>D: COMMIT
    S--xC: 201 (response lost, client times out)
    C->>S: POST /requests (Idempotency-Key: k1)
    S->>D: key k1 for this user? yes, same fingerprint
    S-->>C: 200, same request, Idempotent-Replayed: true
```

Because the key and the request are committed together, there is no
moment where one exists without the other. `BEGIN IMMEDIATE` makes
parallel retries wait their turn, so eight simultaneous calls still create
one request (see `test_concurrent_retries_create_exactly_one_request`).

## Data model

```mermaid
erDiagram
    users ||--o{ requests : "requests"
    applications ||--o{ requests : "has"
    requests ||--o{ events : "history"
    requests |o--o| requests : "supersedes"
    users ||--o{ idempotency_keys : "sent"
    idempotency_keys }o--|| requests : "produced"

    requests {
        text id PK
        text application_id FK
        text requested_by FK
        int discount_bps
        text reason
        text status
        text supersedes_id FK
        text decided_by FK
        text decided_at
    }
    events {
        int seq PK
        text request_id FK
        text type
        text actor_id FK
        text at
        int discount_bps
        text note
    }
    idempotency_keys {
        text user_id PK
        text key PK
        text fingerprint
        text request_id FK
    }
```

Other database rules:

- `one_open_request_per_application` is a partial unique index on
  `requests(application_id)` for open statuses. It backs up the service
  check under concurrency.
- `status` has a `CHECK` constraint with the allowed values.
- Foreign keys are on for every connection.
- Timestamps are ISO 8601 strings in UTC.

## Error mapping

| Error class | HTTP | When |
|---|---|---|
| `InvalidInputError` | 422 | Payload breaks a rule, or the idempotency key is missing or reused |
| `UnauthenticatedError` | 401 | Missing or unknown `X-User-Id` |
| `PermissionDeniedError` | 403 | Wrong role, or deciding your own request |
| `NotFoundError` | 404 | Unknown request or application |
| `ConflictError` | 409 | Request already decided, or the application already has an open request |

The domain and service raise these classes.

Only `api.py` knows about
status codes.
