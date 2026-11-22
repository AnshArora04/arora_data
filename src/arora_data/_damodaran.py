"""The read every Damodaran country function shares: pick the edition in force and its revision, read that
edition's rows, and find one country. Source: Aswath Damodaran, NYU Stern, via macro.damodaran_country_risk.

Private: apps call sovereign_rating(), sovereign_spread(), country_risk_premium() and equity_risk_premium().
Values are his, as published and never recomputed, in fractions (0.0442 is 4.42%).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from arora_data import _readers, damodaran_editions as _editions
from arora_data._db import connection, fetch
from arora_data.catalog import _Row
from arora_data.dates import today_et

_ROWS_SQL = """
SELECT edition_date, revision, entity_code, published_name, entity_type, region, moodys_rating, basis, prs_score,
       default_spread, cds_spread_net, crp, cds_crp, erp, cds_erp
  FROM macro.damodaran_country_risk
 WHERE edition_date = $1 AND revision = $2
 ORDER BY entity_code
"""


class CountryRow(_Row):
    """Which edition and which country a row is. entity_code is ISO 3166-1 alpha-3 (XKX for Kosovo), or ISO 3166-2
    for the three emirates he lists apart from the UAE (AE-AZ, AE-RK, AE-SH, entity_type 'subnational')."""

    edition_date: dt.date
    revision: int
    entity_code: str
    published_name: str
    entity_type: str
    region: str | None


class _CountryRisk(CountryRow):
    moodys_rating: str | None
    basis: str
    prs_score: Decimal | None
    default_spread: Decimal | None
    cds_spread_net: Decimal | None
    crp: Decimal | None
    cds_crp: Decimal | None
    erp: Decimal | None
    cds_erp: Decimal | None


def _pick(editions: list[_editions.CrpEdition], on: dt.date, revision: int | None) -> _editions.CrpEdition:
    """The edition with the latest date on or before on, at revision, or else at its newest revision."""
    if not editions:
        raise ValueError("no Damodaran editions are stored")
    before = [e.edition_date for e in editions if e.edition_date <= on]
    if not before:
        raise ValueError(f"no Damodaran edition on or before {on}: the first is {min(e.edition_date for e in editions)}")
    ed = max(before)
    found = [e for e in editions if e.edition_date == ed and (e.latest if revision is None else e.revision == revision)]
    if len(found) != 1:
        held = ", ".join(str(e.revision) for e in editions if e.edition_date == ed)
        raise ValueError(f"Damodaran's {ed} edition has no revision {revision!r}: it has {held}")
    return found[0]


async def _fetch_rows(edition_date: dt.date, revision: int) -> list[_CountryRisk]:
    async with connection() as conn:
        return [_CountryRisk(**dict(r)) for r in await fetch(conn, _ROWS_SQL, edition_date, revision)]


async def _rows(country: str | None, on: dt.date | None, revision: int | None) -> list[_CountryRisk]:
    """Every row of the edition in force on on (default: today in US/Eastern), or the one row for country."""
    picked = _pick(await _editions.damodaran_editions(), on or today_et(), revision)
    rows = await _fetch_rows(picked.edition_date, picked.revision)
    if country is None:
        return rows
    hint = (f"country in Damodaran's {picked.edition_date} edition: use an ISO alpha-3 code or a name from that "
            "edition, such as USA or 'United States'")
    return [_readers._resolve(country, rows, hint, id="entity_code", name="published_name")]
