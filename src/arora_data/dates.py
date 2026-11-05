"""Dates in US Eastern time, the timezone the database's dates are in."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def today_et(now: datetime | None = None) -> date:
    """Today's date in US Eastern time, never the machine's timezone or UTC. now must have a timezone if given."""
    if now is not None and now.tzinfo is None:
        raise ValueError("now must have a timezone")
    return (now or datetime.now(EASTERN)).astimezone(EASTERN).date()
