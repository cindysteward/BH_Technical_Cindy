"""End-to-end tests over HTTP, the way a client or the UI calls the API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

REASON = "Customer has a competing offer."


def auth_headers(user: str, key: str | None = None) -> dict[str, str]:
    """Build request headers for a user, with an optional idempotency key."""
    request_headers = {"X-User-Id": user}
    if key is not None:
        request_headers["Idempotency-Key"] = key
    return request_headers


def submit(
    client: TestClient,
    user: str = "arthur",
    *,
    key: str = "k1",
    application_id: str = "APP-1001",
    discount_bps: int = 25,
) -> Any:
    """Submit a request over HTTP and return the response."""
    return client.post(
        "/api/requests",
        headers=auth_headers(user, key),
        json={
            "application_id": application_id,
            "discount_bps": discount_bps,
            "reason": REASON,
        },
    )


def decide(
    client: TestClient,
    request_id: str,
    user: str,
    outcome: str,
    note: str | None = None,
) -> Any:
    """Send a decision over HTTP and return the response."""
    return client.post(
        f"/api/requests/{request_id}/decision",
        headers=auth_headers(user),
        json={"outcome": outcome, "note": note},
    )


def test_full_workflow(client: TestClient) -> None:
    """Submit, approve, read the approved discount and check the history."""
    created = submit(client)
    assert created.status_code == 201
    request_id = created.json()["id"]
    assert created.json()["status"] == "pending"

    before = client.get(
        "/api/applications/APP-1001/approved-discount",
        headers=auth_headers("mortgage-engine"),
    )
    assert before.status_code == 404

    approved = decide(
        client, request_id, "tricia", "approved", "Within guidelines."
    )
    assert approved.status_code == 200
    assert approved.json()["decided_by"] == "tricia"
    assert approved.json()["decided_at"] is not None

    discount = client.get(
        "/api/applications/APP-1001/approved-discount",
        headers=auth_headers("mortgage-engine"),
    )
    assert discount.status_code == 200
    assert discount.json() == {
        "application_id": "APP-1001",
        "request_id": request_id,
        "discount_bps": 25,
        "standard_rate_bps": 421,
        "final_rate_bps": 396,
        "approved_by": "tricia",
        "approved_at": approved.json()["decided_at"],
    }

    history = client.get(
        f"/api/requests/{request_id}/history", headers=auth_headers("ford")
    ).json()
    assert history["request"]["status"] == "approved"
    assert [
        (event["type"], event["actor_id"]) for event in history["events"]
    ] == [
        ("submitted", "arthur"),
        ("approved", "tricia"),
    ]


def test_retry_with_the_same_key_returns_the_same_request(
    client: TestClient,
) -> None:
    """A retried create returns 200, the original request and a marker."""
    first = submit(client)
    second = submit(client)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.json()["id"] == first.json()["id"]
    assert (
        len(client.get("/api/requests", headers=auth_headers("arthur")).json())
        == 1
    )


def test_reusing_a_key_for_a_different_payload_returns_422(
    client: TestClient,
) -> None:
    """The same key with another discount is a clear client error."""
    submit(client)
    response = submit(client, discount_bps=30)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "idempotency_key_reused"


def test_approved_discount_is_limited_to_the_mortgage_process(
    client: TestClient,
) -> None:
    """Only the mortgage process may read the approved discount."""
    request_id = submit(client).json()["id"]
    assert decide(client, request_id, "tricia", "approved").status_code == 200
    url = "/api/applications/APP-1001/approved-discount"
    assert client.get(url, headers=auth_headers("arthur")).status_code == 403
    assert client.get(url, headers=auth_headers("tricia")).status_code == 403
    assert (
        client.get(url, headers=auth_headers("mortgage-engine")).status_code
        == 200
    )


def test_changing_the_discount_goes_through_a_revision(
    client: TestClient,
) -> None:
    """A revision replaces the request, and only the new one is approvable."""
    original = submit(client).json()
    revision = client.post(
        f"/api/requests/{original['id']}/revisions",
        headers=auth_headers("arthur", "rev-1"),
        json={
            "discount_bps": 35,
            "reason": "Adjusted after checking the margin.",
        },
    )
    assert revision.status_code == 201
    revised = revision.json()
    assert revised["supersedes_id"] == original["id"]
    assert revised["discount_bps"] == 35

    assert (
        client.get(
            f"/api/requests/{original['id']}", headers=auth_headers("arthur")
        ).json()["status"]
        == "withdrawn"
    )
    assert (
        decide(client, original["id"], "tricia", "approved").status_code == 409
    )
    assert (
        decide(client, revised["id"], "tricia", "approved").status_code == 200
    )

    discount = client.get(
        "/api/applications/APP-1001/approved-discount",
        headers=auth_headers("mortgage-engine"),
    )
    assert discount.json()["discount_bps"] == 35

    history = client.get(
        f"/api/requests/{revised['id']}/history",
        headers=auth_headers("arthur"),
    ).json()
    assert [version["id"] for version in history["versions"]] == [
        original["id"],
        revised["id"],
    ]


def test_revision_retry_is_idempotent(client: TestClient) -> None:
    """Replaying a revision returns the same replacement."""
    original = submit(client).json()
    body = {
        "discount_bps": 35,
        "reason": "Adjusted after checking the margin.",
    }
    path = f"/api/requests/{original['id']}/revisions"
    first = client.post(
        path, headers=auth_headers("arthur", "rev-1"), json=body
    )
    second = client.post(
        path, headers=auth_headers("arthur", "rev-1"), json=body
    )
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


def test_withdraw(client: TestClient) -> None:
    """Withdrawing a pending request frees the application."""
    request_id = submit(client).json()["id"]
    response = client.post(
        f"/api/requests/{request_id}/withdraw", headers=auth_headers("arthur")
    )
    assert response.status_code == 200
    assert response.json()["status"] == "withdrawn"
    assert submit(client, key="k2").status_code == 201


def test_unauthenticated_calls_are_rejected(client: TestClient) -> None:
    """Calls without a known user get 401 with the standard error body."""
    missing = client.get("/api/requests")
    unknown = client.get("/api/requests", headers=auth_headers("nobody"))
    assert missing.status_code == 401
    assert unknown.status_code == 401
    assert missing.json()["error"]["code"] == "unauthenticated"


def test_roles_are_enforced(client: TestClient) -> None:
    """Each role is limited to its own actions, and nobody self-approves."""
    assert submit(client, "tricia").status_code == 403
    request_id = submit(client).json()["id"]
    assert decide(client, request_id, "ford", "approved").status_code == 403

    own = submit(client, "cindy", key="k2", application_id="APP-1002").json()[
        "id"
    ]
    refused = decide(client, own, "cindy", "approved")
    assert refused.status_code == 403
    assert "own" in refused.json()["error"]["message"]


def test_invalid_input_is_reported_clearly(client: TestClient) -> None:
    """Bad payloads get 422 with one consistent error shape."""
    bodies: list[dict[str, Any]] = [
        {"application_id": "APP-1001", "discount_bps": "25", "reason": REASON},
        {
            "application_id": "APP-1001",
            "discount_bps": 25,
            "reason": REASON,
            "extra": 1,
        },
        {"application_id": "APP-1001", "discount_bps": 0, "reason": REASON},
        {"application_id": "APP-1001", "discount_bps": 500, "reason": REASON},
        {"application_id": "APP-1001", "discount_bps": 25, "reason": "short"},
    ]
    for body in bodies:
        response = client.post(
            "/api/requests", headers=auth_headers("arthur", "k"), json=body
        )
        assert response.status_code == 422, body
        assert response.json()["error"]["code"] in {"invalid_input"}

    no_key = client.post(
        "/api/requests",
        headers=auth_headers("arthur"),
        json={
            "application_id": "APP-1001",
            "discount_bps": 25,
            "reason": REASON,
        },
    )
    assert no_key.status_code == 422
    assert "Idempotency-Key" in no_key.json()["error"]["message"]


def test_decline_needs_a_note(client: TestClient) -> None:
    """Declining without an explanation is refused."""
    request_id = submit(client).json()["id"]
    assert decide(client, request_id, "tricia", "declined").status_code == 422
    assert (
        decide(
            client, request_id, "tricia", "declined", "Not justified."
        ).status_code
        == 200
    )


def test_decided_requests_are_final(client: TestClient) -> None:
    """A second reviewer cannot change a decision."""
    request_id = submit(client).json()["id"]
    decide(client, request_id, "tricia", "declined", "No.")
    assert (
        decide(client, request_id, "fenchurch", "approved").status_code == 409
    )


def test_unknown_records_return_404(client: TestClient) -> None:
    """Unknown requests and applications are reported as not found."""
    assert (
        client.get(
            "/api/requests/nope", headers=auth_headers("arthur")
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/applications/APP-9999", headers=auth_headers("arthur")
        ).status_code
        == 404
    )
    assert submit(client, application_id="APP-9999").status_code == 404


def test_requests_can_be_filtered(client: TestClient) -> None:
    """The list endpoint filters by status and application."""
    first = submit(client).json()["id"]
    submit(client, key="k2", application_id="APP-1002")
    decide(client, first, "tricia", "approved")
    approved = client.get(
        "/api/requests?status=approved", headers=auth_headers("arthur")
    ).json()
    assert [request["id"] for request in approved] == [first]
    by_app = client.get(
        "/api/requests?application_id=APP-1002", headers=auth_headers("arthur")
    ).json()
    assert len(by_app) == 1
    assert (
        client.get(
            "/api/requests?status=bogus", headers=auth_headers("arthur")
        ).status_code
        == 422
    )


def test_applications_show_their_open_request(client: TestClient) -> None:
    """Applications show their pending or approved request."""
    submit(client)
    applications = {
        application["id"]: application
        for application in client.get(
            "/api/applications", headers=auth_headers("arthur")
        ).json()
    }
    assert applications["APP-1001"]["open_request_status"] == "pending"
    assert applications["APP-1002"]["open_request_status"] is None


def test_demo_data_is_seeded_on_first_start(tmp_path: Path) -> None:
    """A fresh run has requests in every state, ready to explore."""
    with TestClient(
        create_app(Settings(db_path=tmp_path / "demo.db"))
    ) as demo:
        requests = demo.get(
            "/api/requests", headers=auth_headers("arthur")
        ).json()
        assert {request["status"] for request in requests} == {
            "pending",
            "approved",
            "declined",
            "withdrawn",
        }
        discount = demo.get(
            "/api/applications/APP-1002/approved-discount",
            headers=auth_headers("mortgage-engine"),
        )
        assert discount.status_code == 200


def test_ui_and_health_are_served(client: TestClient) -> None:
    """The UI is reachable and the root redirects to it."""
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/ui/").status_code == 200
    root = client.get("/", follow_redirects=False)
    assert root.status_code in {302, 307}
    assert root.headers["location"] == "/ui/"
