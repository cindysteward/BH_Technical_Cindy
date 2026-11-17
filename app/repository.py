"""Persistence for the service, with a SQLite implementation.

Defines the ``Store`` protocol (queries and writes inside a transaction)
and the ``Database`` protocol (opening a transaction), plus their SQLite
implementations ``SqliteStore`` and ``SqliteDatabase``. The service
depends only on the protocols.

Every use case runs in one transaction. Writes begin with
``BEGIN IMMEDIATE``, which serialises writers, and status changes are
conditional updates, so competing callers cannot both succeed. A partial
unique index allows at most one pending or approved request per
application. Timestamps are stored as ISO 8601 UTC strings. The database
must be a file, because an in-memory database is separate for every
connection.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Protocol

from app.domain import (
    OPEN_STATUSES,
    Application,
    EventType,
    IdempotencyRecord,
    PricingRequest,
    RequestEvent,
    Role,
    Status,
    User,
)
from app.errors import ConflictError

_OPEN_SQL = ", ".join(f"'{status.value}'" for status in sorted(OPEN_STATUSES))
_STATUS_SQL = ", ".join(f"'{status.value}'" for status in Status)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    roles TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    customer_name TEXT NOT NULL,
    standard_rate_bps INTEGER NOT NULL CHECK (standard_rate_bps > 0)
);
CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id),
    requested_by TEXT NOT NULL REFERENCES users(id),
    discount_bps INTEGER NOT NULL CHECK (discount_bps > 0),
    reason TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ({_STATUS_SQL})),
    created_at TEXT NOT NULL,
    supersedes_id TEXT REFERENCES requests(id),
    decided_by TEXT REFERENCES users(id),
    decided_at TEXT,
    decision_note TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_request_per_application
    ON requests(application_id) WHERE status IN ({_OPEN_SQL});
CREATE INDEX IF NOT EXISTS requests_by_status ON requests(status, created_at);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL REFERENCES requests(id),
    type TEXT NOT NULL,
    actor_id TEXT NOT NULL REFERENCES users(id),
    at TEXT NOT NULL,
    discount_bps INTEGER NOT NULL,
    note TEXT
);
CREATE INDEX IF NOT EXISTS events_by_request ON events(request_id, seq);
CREATE TABLE IF NOT EXISTS idempotency_keys (
    user_id TEXT NOT NULL REFERENCES users(id),
    key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    request_id TEXT NOT NULL REFERENCES requests(id),
    PRIMARY KEY (user_id, key)
);
"""


class Store(Protocol):
    """Everything the service reads and writes inside one transaction."""

    def get_user(self, user_id: str) -> User | None:
        """Find a user by id."""
        ...

    def list_users(self) -> list[User]:
        """List all users."""
        ...

    def save_user(self, user: User) -> None:
        """Insert a user, or update it if the id exists."""
        ...

    def get_application(self, application_id: str) -> Application | None:
        """Find an application by id."""
        ...

    def list_applications(self) -> list[Application]:
        """List all applications."""
        ...

    def save_application(self, application: Application) -> None:
        """Insert an application, or update it if the id exists."""
        ...

    def get_request(self, request_id: str) -> PricingRequest | None:
        """Find a request by id."""
        ...

    def find_open_request(self, application_id: str) -> PricingRequest | None:
        """Find the pending or approved request of an application."""
        ...

    def list_open_requests(self) -> list[PricingRequest]:
        """List every pending or approved request."""
        ...

    def list_requests(
        self,
        *,
        status: Status | None = None,
        application_id: str | None = None,
    ) -> list[PricingRequest]:
        """List requests, newest first, optionally filtered."""
        ...

    def insert_request(self, request: PricingRequest) -> None:
        """Store a new request."""
        ...

    def update_request(
        self, request: PricingRequest, *, expected_status: Status
    ) -> None:
        """Save a changed request if it still has the expected status."""
        ...

    def add_event(self, event: RequestEvent) -> None:
        """Append an event to the history."""
        ...

    def list_events(self, request_ids: Sequence[str]) -> list[RequestEvent]:
        """List the events of the given requests in the order they happened."""
        ...

    def find_idempotency_record(
        self, user_id: str, key: str
    ) -> IdempotencyRecord | None:
        """Find the record stored for an idempotency key."""
        ...

    def save_idempotency_record(self, record: IdempotencyRecord) -> None:
        """Remember an idempotency key."""
        ...


class Database(Protocol):
    """Hands out stores that are bound to a transaction."""

    def read(self) -> AbstractContextManager[Store]:
        """Open a read-only transaction."""
        ...

    def write(self) -> AbstractContextManager[Store]:
        """Open a transaction that commits on success, rolls back on error."""
        ...


class SqliteStore:
    """``Store`` backed by one SQLite connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap a connection that is already inside a transaction.

        Args:
            connection: Open SQLite connection with ``sqlite3.Row`` rows.
        """
        self._connection = connection

    def get_user(self, user_id: str) -> User | None:
        """Find a user by id.

        Args:
            user_id: Id to look up.

        Returns:
            The user, or None if it does not exist.
        """
        row = self._connection.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return _to_user(row) if row else None

    def list_users(self) -> list[User]:
        """List all users.

        Returns:
            Users ordered by id.
        """
        rows = self._connection.execute(
            "SELECT * FROM users ORDER BY id"
        ).fetchall()
        return [_to_user(row) for row in rows]

    def save_user(self, user: User) -> None:
        """Insert a user, or update it if the id exists.

        Args:
            user: User to store.
        """
        roles = ",".join(sorted(role.value for role in user.roles))
        self._connection.execute(
            "INSERT INTO users (id, name, roles) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "name = excluded.name, roles = excluded.roles",
            (user.id, user.name, roles),
        )

    def get_application(self, application_id: str) -> Application | None:
        """Find an application by id.

        Args:
            application_id: Id to look up.

        Returns:
            The application, or None if it does not exist.
        """
        row = self._connection.execute(
            "SELECT * FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
        return _to_application(row) if row else None

    def list_applications(self) -> list[Application]:
        """List all applications.

        Returns:
            Applications ordered by id.
        """
        rows = self._connection.execute(
            "SELECT * FROM applications ORDER BY id"
        ).fetchall()
        return [_to_application(row) for row in rows]

    def save_application(self, application: Application) -> None:
        """Insert an application, or update it if the id exists.

        Args:
            application: Application to store.
        """
        self._connection.execute(
            "INSERT INTO applications "
            "(id, customer_name, standard_rate_bps) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "customer_name = excluded.customer_name, "
            "standard_rate_bps = excluded.standard_rate_bps",
            (
                application.id,
                application.customer_name,
                application.standard_rate_bps,
            ),
        )

    def get_request(self, request_id: str) -> PricingRequest | None:
        """Find a request by id.

        Args:
            request_id: Id to look up.

        Returns:
            The request, or None if it does not exist.
        """
        row = self._connection.execute(
            "SELECT * FROM requests WHERE id = ?", (request_id,)
        ).fetchone()
        return _to_request(row) if row else None

    def find_open_request(self, application_id: str) -> PricingRequest | None:
        """Find the pending or approved request of an application.

        Args:
            application_id: Application to look at.

        Returns:
            The open request, or None if the application has none.
        """
        row = self._connection.execute(
            "SELECT * FROM requests WHERE application_id = ? "
            f"AND status IN ({_OPEN_SQL})",
            (application_id,),
        ).fetchone()
        return _to_request(row) if row else None

    def list_open_requests(self) -> list[PricingRequest]:
        """List every pending or approved request.

        Returns:
            Open requests, newest first.
        """
        rows = self._connection.execute(
            f"SELECT * FROM requests WHERE status IN ({_OPEN_SQL}) "
            "ORDER BY created_at DESC"
        ).fetchall()
        return [_to_request(row) for row in rows]

    def list_requests(
        self,
        *,
        status: Status | None = None,
        application_id: str | None = None,
    ) -> list[PricingRequest]:
        """List requests, newest first, optionally filtered.

        Args:
            status: Only return requests with this status.
            application_id: Only return requests for this application.

        Returns:
            Matching requests, newest first.
        """
        clauses: list[str] = []
        params: list[str] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if application_id is not None:
            clauses.append("application_id = ?")
            params.append(application_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection.execute(
            f"SELECT * FROM requests {where} "
            "ORDER BY created_at DESC, rowid DESC",
            params,
        ).fetchall()
        return [_to_request(row) for row in rows]

    def insert_request(self, request: PricingRequest) -> None:
        """Store a new request.

        Args:
            request: Request to insert.

        Raises:
            ConflictError: If the application already has an open request.
        """
        try:
            self._connection.execute(
                "INSERT INTO requests (id, application_id, requested_by, "
                "discount_bps, reason, status, created_at, supersedes_id, "
                "decided_by, decided_at, decision_note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request.id,
                    request.application_id,
                    request.requested_by,
                    request.discount_bps,
                    request.reason,
                    request.status.value,
                    request.created_at.isoformat(),
                    request.supersedes_id,
                    request.decided_by,
                    _format_time(request.decided_at),
                    request.decision_note,
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                raise ConflictError(
                    "This application already has an open request."
                ) from exc
            raise

    def update_request(
        self, request: PricingRequest, *, expected_status: Status
    ) -> None:
        """Save a changed request if it still has the expected status.

        Args:
            request: Request with its new status and decision fields.
            expected_status: Status the stored request must still have.

        Raises:
            ConflictError: If someone else changed the request in the meantime.
        """
        cursor = self._connection.execute(
            "UPDATE requests SET status = ?, decided_by = ?, "
            "decided_at = ?, decision_note = ? "
            "WHERE id = ? AND status = ?",
            (
                request.status.value,
                request.decided_by,
                _format_time(request.decided_at),
                request.decision_note,
                request.id,
                expected_status.value,
            ),
        )
        if cursor.rowcount != 1:
            raise ConflictError(
                "The request was changed by someone else. "
                "Reload and try again."
            )

    def add_event(self, event: RequestEvent) -> None:
        """Append an event to the history.

        Args:
            event: Event to store.
        """
        self._connection.execute(
            "INSERT INTO events "
            "(request_id, type, actor_id, at, discount_bps, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                event.request_id,
                event.type.value,
                event.actor_id,
                event.at.isoformat(),
                event.discount_bps,
                event.note,
            ),
        )

    def list_events(self, request_ids: Sequence[str]) -> list[RequestEvent]:
        """List the events of the given requests in the order they happened.

        Args:
            request_ids: Requests whose events to return.

        Returns:
            Events ordered by when they were recorded.
        """
        if not request_ids:
            return []
        marks = ", ".join("?" for _ in request_ids)
        rows = self._connection.execute(
            f"SELECT * FROM events WHERE request_id IN ({marks}) ORDER BY seq",
            list(request_ids),
        ).fetchall()
        return [_to_event(row) for row in rows]

    def find_idempotency_record(
        self, user_id: str, key: str
    ) -> IdempotencyRecord | None:
        """Find the record stored for an idempotency key.

        Args:
            user_id: User who sent the key.
            key: The idempotency key.

        Returns:
            The stored record, or None if the key is new.
        """
        row = self._connection.execute(
            "SELECT * FROM idempotency_keys WHERE user_id = ? AND key = ?",
            (user_id, key),
        ).fetchone()
        if row is None:
            return None
        return IdempotencyRecord(
            row["user_id"], row["key"], row["fingerprint"], row["request_id"]
        )

    def save_idempotency_record(self, record: IdempotencyRecord) -> None:
        """Remember an idempotency key.

        Args:
            record: Key, payload fingerprint and the request it produced.
        """
        self._connection.execute(
            "INSERT INTO idempotency_keys "
            "(user_id, key, fingerprint, request_id) VALUES (?, ?, ?, ?)",
            (
                record.user_id,
                record.key,
                record.fingerprint,
                record.request_id,
            ),
        )


class SqliteDatabase:
    """``Database`` that opens one SQLite connection per transaction."""

    def __init__(self, path: str | Path) -> None:
        """Create the database file and its tables if they do not exist.

        Args:
            path: Location of the SQLite file. Parent folders are created.
        """
        self._path = str(path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.executescript(SCHEMA)
        finally:
            connection.close()

    @contextmanager
    def read(self) -> Iterator[Store]:
        """Open a read-only transaction.

        Yields:
            A store bound to the transaction.
        """
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            yield SqliteStore(connection)
        finally:
            connection.rollback()
            connection.close()

    @contextmanager
    def write(self) -> Iterator[Store]:
        """Open a read-write transaction.

        Yields:
            A store bound to the transaction. It commits when the block ends
            normally and rolls back if the block raises.
        """
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield SqliteStore(connection)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        """Open a connection with the settings this service relies on.

        Returns:
            Connection in manual transaction mode with foreign keys and WAL on.
        """
        connection = sqlite3.connect(
            self._path, isolation_level=None, timeout=10.0
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection


def _format_time(moment: datetime | None) -> str | None:
    """Format an optional datetime for storage.

    Args:
        moment: Datetime to format, or None.

    Returns:
        ISO 8601 string, or None.
    """
    return moment.isoformat() if moment else None


def _parse_time(stored: str | None) -> datetime | None:
    """Read an optional stored datetime.

    Args:
        stored: ISO 8601 string, or None.

    Returns:
        Parsed datetime, or None.
    """
    return datetime.fromisoformat(stored) if stored else None


def _to_user(row: sqlite3.Row) -> User:
    """Convert a ``users`` row to a domain object.

    Args:
        row: Row from the ``users`` table.

    Returns:
        The matching user.
    """
    roles = frozenset(
        Role(value) for value in row["roles"].split(",") if value
    )
    return User(row["id"], row["name"], roles)


def _to_application(row: sqlite3.Row) -> Application:
    """Convert an ``applications`` row to a domain object.

    Args:
        row: Row from the ``applications`` table.

    Returns:
        The matching application.
    """
    return Application(
        row["id"], row["customer_name"], row["standard_rate_bps"]
    )


def _to_request(row: sqlite3.Row) -> PricingRequest:
    """Convert a ``requests`` row to a domain object.

    Args:
        row: Row from the ``requests`` table.

    Returns:
        The matching request.
    """
    return PricingRequest(
        id=row["id"],
        application_id=row["application_id"],
        requested_by=row["requested_by"],
        discount_bps=row["discount_bps"],
        reason=row["reason"],
        status=Status(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        supersedes_id=row["supersedes_id"],
        decided_by=row["decided_by"],
        decided_at=_parse_time(row["decided_at"]),
        decision_note=row["decision_note"],
    )


def _to_event(row: sqlite3.Row) -> RequestEvent:
    """Convert an ``events`` row to a domain object.

    Args:
        row: Row from the ``events`` table.

    Returns:
        The matching event.
    """
    return RequestEvent(
        request_id=row["request_id"],
        type=EventType(row["type"]),
        actor_id=row["actor_id"],
        at=datetime.fromisoformat(row["at"]),
        discount_bps=row["discount_bps"],
        note=row["note"],
    )
