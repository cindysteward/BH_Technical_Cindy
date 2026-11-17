"""Run an end-to-end scenario against a running service.

Start the service with ``make run`` and run ``make demo`` in a second
terminal. The script uses only the public HTTP API and the standard
library. It picks applications without an open request, so it can run
repeatedly.

The scenario follows one request through its life. A relationship manager
submits it, retries after a timeout and changes the discount. A reviewer
approves it, and the mortgage process reads the approved discount. The
script then checks the calls the rules must refuse and prints the
history. Each step verifies its result, so the script fails when
behaviour changes.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Reply:
    """An HTTP response as the demo needs it.

    Attributes:
        status: HTTP status code.
        body: Parsed JSON body.
        headers: Response headers with lower-case names.
    """

    status: int
    body: Any
    headers: dict[str, str]


class Client:
    """Minimal JSON client for the pricing exception API."""

    def __init__(self, base_url: str) -> None:
        """Remember where the service runs.

        Args:
            base_url: Root URL of the service, for example ``http://localhost:8000``.
        """
        self._base = base_url.rstrip("/") + "/api"

    def call(
        self,
        method: str,
        path: str,
        user: str,
        body: dict[str, Any] | None = None,
        key: str | None = None,
    ) -> Reply:
        """Send one request.

        Args:
            method: HTTP method.
            path: Path under ``/api``.
            user: Value for the ``X-User-Id`` header.
            body: JSON body, if any.
            key: Value for the ``Idempotency-Key`` header, if any.

        Returns:
            The response, including error responses.
        """
        headers = {"Content-Type": "application/json", "X-User-Id": user}
        if key:
            headers["Idempotency-Key"] = key
        payload = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self._base + path, data=payload, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request) as response:
                return Reply(
                    response.status,
                    json.load(response),
                    {
                        name.lower(): value
                        for name, value in response.headers.items()
                    },
                )
        except urllib.error.HTTPError as error:
            return Reply(
                error.code,
                json.load(error),
                {name.lower(): value for name, value in error.headers.items()},
            )


def print_step(number: int, text: str, *, pause: bool) -> None:
    """Print a numbered step heading.

    Args:
        number: Step number.
        text: What the step does.
        pause: Wait for Enter first, except before the first step.
    """
    if pause and number > 1:
        input("\n   Press Enter for the next step...")
    print(f"\n{number}. {text}")


def expect(condition: bool, message: str) -> None:
    """Print a result line, or stop the demo if the expectation fails.

    Args:
        condition: Whether the expectation holds.
        message: What was expected.
    """
    if not condition:
        print(f"   ✗ {message}")
        sys.exit(1)
    print(f"   ✓ {message}")


def run_scenario(client: Client, *, pause: bool) -> None:
    """Run the scenario from start to finish.

    Args:
        client: Client pointing at a running service.
        pause: Wait for Enter between steps.
    """
    applications = client.call("GET", "/applications", "arthur").body
    free = [
        application["id"]
        for application in applications
        if not application["open_request_id"]
    ]
    if len(free) < 2:
        print(
            "Not enough applications without an open request. "
            "Run `make reset` and try again."
        )
        sys.exit(1)
    application, other = free[0], free[1]

    key = str(uuid.uuid4())
    body = {
        "application_id": application,
        "discount_bps": 25,
        "reason": "Customer has a competing offer.",
    }

    print_step(
        1,
        f"Arthur (relationship manager) asks for 25 bps off {application}",
        pause=pause,
    )
    first = client.call("POST", "/requests", "arthur", body, key)
    expect(
        first.status == 201,
        f"created request {first.body['id'][:8]}… "
        f"with status {first.body['status']}",
    )

    print_step(
        2,
        "The call times out and is retried with the same Idempotency-Key",
        pause=pause,
    )
    retry = client.call("POST", "/requests", "arthur", body, key)
    expect(
        retry.status == 200 and retry.body["id"] == first.body["id"],
        "got the same request back, no duplicate",
    )
    expect(
        retry.headers.get("idempotent-replayed") == "true",
        "response is marked as a replay",
    )

    print_step(
        3,
        "Arthur realises 30 bps is needed and changes the discount",
        pause=pause,
    )
    revised = client.call(
        "POST",
        f"/requests/{first.body['id']}/revisions",
        "arthur",
        {
            "discount_bps": 30,
            "reason": "Competing offer is lower than we thought.",
        },
        str(uuid.uuid4()),
    )
    expect(
        revised.status == 201,
        f"new request {revised.body['id'][:8]}… replaces the old one",
    )
    old = client.call("GET", f"/requests/{first.body['id']}", "arthur").body
    expect(
        old["status"] == "withdrawn",
        "the original 25 bps request is now withdrawn",
    )

    print_step(
        4,
        "Tricia (reviewer) tries to approve the old version she had open",
        pause=pause,
    )
    stale = client.call(
        "POST",
        f"/requests/{first.body['id']}/decision",
        "tricia",
        {"outcome": "approved"},
    )
    expect(stale.status == 409, f"refused: {stale.body['error']['message']}")

    print_step(5, "Tricia approves the current version", pause=pause)
    approved = client.call(
        "POST",
        f"/requests/{revised.body['id']}/decision",
        "tricia",
        {"outcome": "approved"},
    )
    expect(
        approved.status == 200,
        f"approved by {approved.body['decided_by']} "
        f"at {approved.body['decided_at']}",
    )

    print_step(
        6,
        "The mortgage application process asks for the approved discount",
        pause=pause,
    )
    discount = client.call(
        "GET",
        f"/applications/{application}/approved-discount",
        "mortgage-engine",
    ).body
    expect(
        discount["discount_bps"] == 30,
        f"30 bps approved, rate {discount['standard_rate_bps']} → "
        f"{discount['final_rate_bps']} bps",
    )

    print_step(7, "Rules that must hold", pause=pause)
    dana_key = str(uuid.uuid4())
    own = client.call(
        "POST",
        "/requests",
        "cindy",
        {
            "application_id": other,
            "discount_bps": 15,
            "reason": "Loyal customer for twelve years.",
        },
        dana_key,
    )
    self_approval = client.call(
        "POST",
        f"/requests/{own.body['id']}/decision",
        "cindy",
        {"outcome": "approved"},
    )
    expect(self_approval.status == 403, "Cindy cannot approve her own request")
    overturn = client.call(
        "POST",
        f"/requests/{revised.body['id']}/decision",
        "fenchurch",
        {"outcome": "declined", "note": "No."},
    )
    expect(
        overturn.status == 409, "Fenchurch cannot overturn Tricia's decision"
    )
    duplicate = client.call(
        "POST",
        "/requests",
        "ford",
        {**body, "discount_bps": 10},
        str(uuid.uuid4()),
    )
    expect(
        duplicate.status == 409,
        f"Ford cannot open a second request on {application}",
    )

    print_step(8, "Full history of the approved request", pause=pause)
    history = client.call(
        "GET", f"/requests/{revised.body['id']}/history", "ford"
    ).body
    for event in history["events"]:
        print(
            f"   {event['at'][:19]}  {event['type']:<9} "
            f"by {event['actor_id']:<6} {event['discount_bps']} bps"
        )
    print(
        "\nDemo finished. Open http://localhost:8000 to see the same "
        "data in the UI."
    )


def main() -> None:
    """Parse arguments and run the demo."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--step",
        action="store_true",
        help="wait for Enter between steps",
    )
    options = parser.parse_args()
    try:
        run_scenario(Client(options.base_url), pause=options.step)
    except urllib.error.URLError:
        print(
            f"Cannot reach {options.base_url}. "
            "Start the service with `make run` first."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
