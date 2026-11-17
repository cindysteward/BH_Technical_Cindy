"""Runtime settings, read from environment variables.

``Settings`` holds the database path, the largest allowed discount and
whether example requests are created on first start. Each value has a
default, so the service runs without any configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuration for one running instance of the service.

    Attributes:
        db_path: Location of the SQLite database file.
        max_discount_bps: Largest discount in basis points a request may
            ask for.
        seed_demo_requests: Whether to create example requests on first start.
    """

    db_path: Path = Path("data/pricing.db")
    max_discount_bps: int = 100
    seed_demo_requests: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from ``PRICING_*`` environment variables.

        Returns:
            Settings with any environment overrides applied to the defaults.
        """
        defaults = cls()
        return cls(
            db_path=Path(
                os.environ.get("PRICING_DB_PATH", str(defaults.db_path))
            ),
            max_discount_bps=int(
                os.environ.get(
                    "PRICING_MAX_DISCOUNT_BPS", str(defaults.max_discount_bps)
                )
            ),
            seed_demo_requests=os.environ.get(
                "PRICING_SEED_DEMO", "true"
            ).lower()
            == "true",
        )
