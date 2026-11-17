"""Shared fixtures for the test suite.

Provides an isolated SQLite database for every test, a deterministic
clock that advances one second per call, a service and an API client
built on them, and the seeded users.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain import Policy, User
from app.main import create_app
from app.repository import SqliteDatabase
from app.seed import seed_reference_data
from app.service import PricingExceptionService


class FakeClock:
    """A clock that moves forward one second every time it is read."""

    def __init__(self) -> None:
        """Start at a fixed point in time."""
        self._now = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        """Read the clock.

        Returns:
            The current fake time. The next read is one second later.
        """
        current = self._now
        self._now += timedelta(seconds=1)
        return current


@pytest.fixture
def db(tmp_path: Path) -> SqliteDatabase:
    """Create a database with only the reference data."""
    database = SqliteDatabase(tmp_path / "test.db")
    seed_reference_data(database)
    return database


@pytest.fixture
def service(db: SqliteDatabase) -> PricingExceptionService:
    """Create a service over the test database with a fake clock."""
    return PricingExceptionService(db, policy=Policy(), clock=FakeClock())


@pytest.fixture
def users(service: PricingExceptionService) -> dict[str, User]:
    """Load the seeded users by id."""
    return {user.id: user for user in service.list_users()}


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """Create an HTTP client for an app without demo requests."""
    settings = Settings(db_path=tmp_path / "api.db", seed_demo_requests=False)
    with TestClient(create_app(settings)) as test_client:
        yield test_client
