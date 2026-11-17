"""Reference data and example requests for local runs.

``seed_reference_data`` loads users and applications into an empty
database. ``seed_demo_requests`` adds example requests through the
service, so their history matches history created by a person. The
examples cover every status on APP-1001 to APP-1004, and APP-1005 to
APP-1012 stay free for new requests.
"""

from __future__ import annotations

from app.domain import Application, Role, Status, User
from app.repository import Database
from app.service import PricingExceptionService

USERS = (
    User("arthur", "Arthur Dent", frozenset({Role.RELATIONSHIP_MANAGER})),
    User("ford", "Ford Prefect", frozenset({Role.RELATIONSHIP_MANAGER})),
    User("tricia", "Tricia McMillan", frozenset({Role.REVIEWER})),
    User("fenchurch", "Fenchurch", frozenset({Role.REVIEWER})),
    User(
        "cindy",
        "Cindy Steward",
        frozenset({Role.RELATIONSHIP_MANAGER, Role.REVIEWER}),
    ),
    User(
        "mortgage-engine",
        "Mortgage application process",
        frozenset({Role.SYSTEM}),
    ),
)

APPLICATIONS = (
    Application("APP-1001", "Prosser household", 421),
    Application("APP-1002", "Lunkwill & Fook", 435),
    Application("APP-1003", "Watson household", 398),
    Application("APP-1004", "Russell household", 412),
    Application("APP-1005", "Andrews & McKenna", 440),
    Application("APP-1006", "Mueller household", 405),
    Application("APP-1007", "Fitzmelton household", 429),
    Application("APP-1008", "Hunter & Prosser", 417),
    Application("APP-1009", "McKenna household", 433),
    Application("APP-1010", "Watson & Russell", 409),
    Application("APP-1011", "Andrews household", 426),
    Application("APP-1012", "Hunter household", 414),
)


def seed_reference_data(db: Database) -> None:
    """Create users and applications if the database has none.

    Args:
        db: Database to fill.
    """
    with db.write() as store:
        if store.list_users():
            return
        for user in USERS:
            store.save_user(user)
        for application in APPLICATIONS:
            store.save_application(application)


def seed_demo_requests(service: PricingExceptionService) -> None:
    """Create example requests in each state, unless some already exist.

    Args:
        service: Service used to create the requests.
    """
    if service.list_requests():
        return
    arthur = service.authenticate("arthur")
    ford = service.authenticate("ford")
    tricia = service.authenticate("tricia")
    fenchurch = service.authenticate("fenchurch")

    service.create_request(
        arthur,
        idempotency_key="seed-1",
        application_id="APP-1001",
        discount_bps=25,
        reason="Customer has a competing offer from another bank.",
    )

    approved = service.create_request(
        ford,
        idempotency_key="seed-2",
        application_id="APP-1002",
        discount_bps=40,
        reason="Long-standing customer with savings and a current account.",
    )
    service.decide(
        tricia,
        approved.request.id,
        outcome=Status.APPROVED,
        note="Within guidelines.",
    )

    first = service.create_request(
        arthur,
        idempotency_key="seed-3",
        application_id="APP-1003",
        discount_bps=30,
        reason="Customer is moving their pension accounts to us.",
    )
    service.revise_request(
        arthur,
        first.request.id,
        idempotency_key="seed-3b",
        discount_bps=20,
        reason="Pension transfer confirmed. Lower ask after margin check.",
    )

    declined = service.create_request(
        ford,
        idempotency_key="seed-4",
        application_id="APP-1004",
        discount_bps=60,
        reason="Customer asked for a better rate than their friend received.",
    )
    service.decide(
        fenchurch,
        declined.request.id,
        outcome=Status.DECLINED,
        note="No supporting reason beyond the ask.",
    )
