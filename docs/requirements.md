# Requirements

What the service must do, written as user stories with acceptance
criteria. Every criterion points to the test that proves it, so a failing
test tells you which promise broke.

## Users

| Who | Wants to |
|---|---|
| Relationship manager (RM) | Get a better rate for a customer, and change the ask if needed |
| Reviewer | Decide on requests quickly, with the full picture |
| Mortgage application process | Know the approved discount for an application |
| Any internal user | See where a request stands and how it got there |

## User stories

### US1. Submit a request

*As an RM, I want to request a discount for an existing application, so a
reviewer can decide on it.*

| Acceptance criterion | Test |
|---|---|
| A valid request is created as `pending` with a `submitted` event | `test_submit_creates_pending_request_and_event` |
| Only RMs can submit | `test_only_relationship_managers_can_submit` |
| The application must exist | `test_unknown_application_is_not_found` |
| Discount is 1 to 100 bps and below the standard rate | `test_discount_must_be_within_policy_and_below_the_rate` |
| Reason has at least 10 characters | `test_reason_must_be_meaningful` |
| An application has at most one pending or approved request | `test_invariant_one_open_request_per_application` |

### US2. Retry safely after a timeout

*As an upstream system, I want to resend a call that timed out without
creating a duplicate.*

| Acceptance criterion | Test |
|---|---|
| Same key and body returns the original request with `200` | `test_retry_with_the_same_key_returns_the_same_request` |
| Same key with a different body is refused with `422` | `test_reusing_a_key_for_a_different_payload_returns_422` |
| A missing or oversized key is refused | `test_a_missing_or_oversized_key_is_refused` |
| Keys are scoped per user | `test_idempotency_keys_are_scoped_per_user` |
| Parallel retries still create one request | `test_concurrent_retries_create_exactly_one_request` |

### US3. Change the discount

*As an RM, I want to ask for a different discount while my request is
pending, without the reviewer approving the wrong amount.*

| Acceptance criterion | Test |
|---|---|
| The old request is withdrawn and a new one links back to it | `test_invariant_revision_replaces_the_request_and_links_back` |
| The old version can no longer be approved | `test_invariant_an_approval_applies_to_the_request_that_was_reviewed` |
| Only the owner can change their request | `test_only_the_owner_can_revise_or_withdraw` |
| An invalid change leaves the original untouched | `test_a_failed_revision_changes_nothing` |
| Retrying a change is safe | `test_revision_retry_returns_the_same_replacement` |

### US4. Withdraw a request

*As an RM, I want to withdraw a request I no longer need.*

| Acceptance criterion | Test |
|---|---|
| Only the owner can withdraw, and only while pending | `test_withdraw_is_limited_to_the_owner_and_pending_requests` |
| Withdrawing frees the application for a new request | `test_withdraw` |

### US5. Approve or decline

*As a reviewer, I want to approve or decline a pending request, with my
name and the time on record.*

| Acceptance criterion | Test |
|---|---|
| The decision records who decided and when | `test_approval_records_who_and_when` |
| Only reviewers can decide | `test_only_reviewers_can_decide` |
| Nobody decides their own request | `test_invariant_reviewer_cannot_decide_own_request` |
| Declining needs a note | `test_decline_requires_a_note` |
| A decision is final, also for other reviewers | `test_invariant_a_decided_request_is_final`, `test_another_reviewer_cannot_overturn_a_decision` |
| Two reviewers deciding at once leave one decision | `test_concurrent_decisions_have_a_single_winner` |

### US6. Read the approved discount

*As the mortgage application process, I want the approved discount for an
application, so I can price the mortgage.*

| Acceptance criterion | Test |
|---|---|
| Nothing is returned until a request is approved | `test_approved_discount_is_only_available_once_approved` |
| The answer has the discount, final rate, approver and time | `test_approved_discount_reports_decision_details`, `test_full_workflow` |
| Only the mortgage process can read it | `test_only_the_mortgage_process_can_read_approved_discounts`, `test_approved_discount_is_limited_to_the_mortgage_process` |

### US7. See the history

*As an internal user, I want to see a request's current status and
everything that happened to it.*

| Acceptance criterion | Test |
|---|---|
| History shows every version and every event, in order | `test_invariant_history_records_every_change` |
| Lists can be filtered by status and application | `test_requests_can_be_filtered` |

## Main use cases

| # | Scenario | Flow |
|---|---|---|
| UC1 | Happy path | RM submits, reviewer approves, mortgage process reads the discount |
| UC2 | Timeout | Upstream system submits, gets no answer, retries with the same key, gets the same request |
| UC3 | Change of mind | RM submits 25 bps, revises to 30 bps, reviewer approves the 30 bps version |
| UC4 | Rejection | Reviewer declines with a note, RM sees why and may submit a new request |

`make demo` runs UC1 to UC3 against a live server.

## Edge cases

| Situation | Behaviour |
|---|---|
| Reviewer approves a version that was just revised | `409`, reviewer reloads |
| RM has both roles and tries to approve their own request | `403` |
| Second request for an application that is already approved | `409` |
| Discount sent as a string (`"25"`) | `422`, no silent conversion |
| Unknown field in the body | `422` |
| Same decision sent twice | Returns the request unchanged, no second event |

## Non-functional requirements

| Requirement | How it is met |
|---|---|
| Runs locally with one command | `make install`, `make run`, SQLite, seeded data |
| Every change is auditable | Append-only `events` table, written with the change |
| Safe under parallel calls | Write transactions, unique index, conditional updates |
| Rules cannot silently disappear | `docs/invariants.md` plus the CI check |

Out of scope for this version: real authentication, revoking approvals,
notifications, pagination. Would incorporate if I where to continue on this project.
