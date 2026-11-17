"""Errors raised by the service for expected failures.

Every error carries a short machine-readable ``code`` and a message that
is safe to show to a user. The classes are grouped by cause: invalid
input, missing or insufficient identity, unknown resources and conflicts
with the current state. The HTTP layer maps each class to a status code.
"""

from __future__ import annotations


class PricingError(Exception):
    """Base class for every expected failure that is caused by the caller."""

    code = "error"

    def __init__(self, message: str) -> None:
        """Store the message that will be shown to the caller.

        Args:
            message: Explanation that is safe to show to a user.
        """
        super().__init__(message)
        self.message = message


class InvalidInputError(PricingError):
    """The request is well formed but breaks a business rule."""

    code = "invalid_input"


class IdempotencyKeyReusedError(InvalidInputError):
    """An idempotency key was reused with a different payload."""

    code = "idempotency_key_reused"


class UnauthenticatedError(PricingError):
    """The caller did not say who they are, or the user is unknown."""

    code = "unauthenticated"


class PermissionDeniedError(PricingError):
    """The caller is known but is not allowed to do this."""

    code = "forbidden"


class NotFoundError(PricingError):
    """The requested record does not exist."""

    code = "not_found"


class ConflictError(PricingError):
    """The action clashes with the current state of the data."""

    code = "conflict"
