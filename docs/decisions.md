# Decisions

Each entry records what was decided and why (incl the cost). Add a new
entry instead of rewriting an old one when a decision changes. Context, decision, and Consequences.

## D1. Submitted requests never change

**Context.** "One stakeholder wants a manager to change the discount after
submitting. Another wants every approval to apply to the exact request
that was reviewed." So, if a request could be edited in place, a reviewer might approve something bps while manager changes it to 40 bps at the same time.

**Decision.** Once submitted, a request's discount and reason are fixed/frozen.
To ask for something different, the manager submits a revision. In one
transaction, the service withdraws the current request and creates a new
one with `supersedes_id` pointing back. Only pending requests can be
revised.

**Consequences.**
- An approval always refers to a request whose content never changed.
- The manager can still change their mind before a decision.
- The history endpoint follows the `supersedes_id` chain, so every
  version stays visible.
- A reviewer working on an old version gets a `409` (on purpose) and has to reload.

## D2. Idempotency keys for creating requests

**Context.** Upstream systems retry after a timeout. Without protection, a
retry creates a second request.

**Decision.** `POST /requests` and `POST /requests/{id}/revisions` require
an `Idempotency-Key` header. The key is stored per user, together with a
SHA-256 fingerprint of the operation and payload, in the same transaction
as the request it created.

| Situation | Response |
|---|---|
| New key | `201`, new request |
| Same key, same payload | `200`, original request, `Idempotent-Replayed: true` |
| Same key, different payload | `422 idempotency_key_reused` |
| No key | `422` |

Decisions and withdrawals do not need a key. Repeating the same decision
by the same reviewer returns the request unchanged, so a retry is already
safe.

**Consequences.** Keys are kept forever in this version. Production would need an expiry.

## D3. At most one open request per application

**Context.** Two pending requests for the same application would leave the
mortgage process guessing which discount applies.

**Decision.** An application has at most one request that is pending or
approved. The service checks this, and a partial unique index in SQLite
enforces it as well, so two parallel calls cannot both succeed.

**Consequences.** After an approval, no new request can be made for that
application. Changing an approved discount needs a revoke flow, which I
considered out of scope.

## D4. Current state plus an append-only event log

**Context.** Users need the current status and the full history, with who
did what and when.

**Decision.** The `requests` table holds the current state. The `events`
table records every change (submitted, approved, declined, withdrawn).
Each event stores who acted and when, and the discount at that moment.
State and event are written in the same transaction. Events are never updated or deleted.

**Consequences.** Reads stay simple, and the history cannot drift from the
state. Full event sourcing would add replay logic that this service does
not need.

## D5. Identity from a header, roles from seeded users

**Context.** Real sign-in is outside the exercise, but who did what still
matters.

**Decision.** Every call sends `X-User-Id`. Users and roles are seeded.
A person can be a relationship manager or a reviewer, and may hold both
roles. The mortgage process has its own `system` role, and only that role can
read approved discounts. A user can never decide their own request, even
with both roles.

**Consequences.** Anyone who can reach the API can claim any identity.
This is acceptable locally and must be replaced before production.

## D6. SQLite with one connection per transaction

**Context.** The service has to run locally with no setup.

**Decision.** SQLite in WAL mode. Each use case opens a connection, runs
one transaction and closes it. Writes start with `BEGIN IMMEDIATE`, so
SQLite serialises them. That makes "check the key, then insert" safe
without extra locking. Status changes use `WHERE status = 'pending'`, so
two reviewers deciding at once cannot both win.

**Consequences.** Write throughput is limited to one writer at a time.
That is plenty for an internal approval tool. Storage sits behind the
`Store` protocol, so moving to PostgreSQL only touches `repository.py`.

## D7. Python and FastAPI

**Context.** The team knows Java and C#. Neither is required.

**Decision.** Python 3.11 with FastAPI. It gives typed request models and
OpenAPI docs with very little code, and it runs anywhere with one command.

**Consequences.** The layering (domain, service, repository, API) maps
directly onto a Spring Boot or ASP.NET project if the team wants to port
it.

## D8. Policy rules for a valid request

**Decision.** A discount is a whole number of basis points from 1 to 100,
and must stay below the application's standard rate. The upper limit is
set with `PRICING_MAX_DISCOUNT_BPS`. A reason needs at least 10
characters. Declining needs a note, approving does not.

**Consequences.** Amounts are integers end to end, so there is no
floating point rounding. The limits are placeholders until the business
gives real numbers.

## D9. One server for API and UI

**Decision.** The UI is plain HTML and JavaScript served by the same
FastAPI app under `/ui`. It uses the public API only.

**Consequences.** No build step and no second process. The UI cannot do
anything a client could not do through the API.
