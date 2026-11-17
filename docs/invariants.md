# Invariants

These rules must hold after every change. Each one has at least one test,
and `make check` fails if a listed test disappears or a new
`test_invariant_*` test is not added here.

| ID | Rule | Tests |
|----|------|-------|
| I1 | A retried create with the same idempotency key returns the original request and creates nothing new. | `test_invariant_retry_with_same_key_returns_the_original` |
| I2 | An application has at most one open (pending or approved) request. | `test_invariant_one_open_request_per_application`, `test_invariant_database_refuses_two_open_requests_for_one_application` |
| I3 | A submitted request never changes its discount or reason. A change creates a new request that links to the old one. | `test_invariant_revision_replaces_the_request_and_links_back` |
| I4 | An approval always applies to the exact request the reviewer saw. | `test_invariant_an_approval_applies_to_the_request_that_was_reviewed` |
| I5 | Nobody decides their own request. | `test_invariant_reviewer_cannot_decide_own_request` |
| I6 | A decision is final. | `test_invariant_a_decided_request_is_final` |
| I7 | Every change is recorded with who made it and when. | `test_invariant_history_records_every_change` |

## Changing this list

Add a row when a new rule appears, together with a test named
`test_invariant_<rule>`. Removing a row needs the same review as changing
the rule itself.
