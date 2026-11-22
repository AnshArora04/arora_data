"""Damodaran's country risk premium editions. Source: Aswath Damodaran, NYU Stern, via macro.damodaran_crp_editions.

Damodaran posts an edition of his country risk premium workbook every January, with extras some years (April and
July in 2026), each labelled with its "as of" date. He sometimes re-posts an edition's file with corrections under
the same date: each distinct file the main repo found is a revision of that edition. Revisions are numbered in the
order they were loaded, not posted, so the newest is the one first seen last; latest marks it. Units are fractions,
as published (0.042 is 4.2%).

first_seen_at is the earliest evidence of the file (a Wayback capture or the main repo's first fetch), in US/Eastern,
so for files found late (April and July 2026, first seen 2026-11-12) it is not when he posted them.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from arora_data._db import connection, fetch
from arora_data.catalog import _Row
from arora_data.dates import EASTERN

# Newest revision per edition by first_seen_at, the rule macro.damodaran_country_risk_latest_revision uses.
_EDITIONS_SQL = """
SELECT edition_date, revision, file_name, first_seen_at, mature_erp, us_erp, vol_multiplier, cds_benchmark,
       editor_note,
       row_number() OVER (PARTITION BY edition_date ORDER BY first_seen_at DESC, revision DESC) = 1 AS latest
  FROM macro.damodaran_crp_editions
 ORDER BY edition_date, first_seen_at, revision
"""


class CrpEdition(_Row):
    """One revision of one edition. mature_erp is his mature-market premium, us_erp the US's (stored from 2026),
    vol_multiplier the equity-to-bond volatility ratio that scales default spreads into country premiums."""

    edition_date: dt.date
    revision: int
    file_name: str
    first_seen_at: dt.datetime
    mature_erp: Decimal
    us_erp: Decimal | None
    vol_multiplier: Decimal | None
    cds_benchmark: str | None
    editor_note: str | None
    latest: bool


async def damodaran_editions() -> list[CrpEdition]:
    """Every stored edition at every revision, oldest first, latest marking each edition's newest. Source: Aswath
    Damodaran, NYU Stern."""
    async with connection() as conn:
        return [CrpEdition(**{**dict(r), "first_seen_at": r["first_seen_at"].astimezone(EASTERN)})
                for r in await fetch(conn, _EDITIONS_SQL)]
