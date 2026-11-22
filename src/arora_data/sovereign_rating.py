"""Sovereign credit ratings: the rating behind each country's spread and premiums. Source: Aswath Damodaran, NYU
Stern, via macro.damodaran_country_risk.

Each row is as he published it, never recomputed:
- basis: which way he rated the country, 'rating' (Moody's) or 'prs' (no Moody's rating, so the PRS Group's
  composite country risk score stands in).
- moodys_rating: the Moody's rating he uses (Aaa best, C worst), set exactly when basis is 'rating'. Where Moody's
  has none or withdrew it, his file maps S&P's rating onto Moody's scale: in 2026-07-01 Venezuela's Moody's is
  withdrawn, so S&P's CCC- gives Caa3.
- prs_score: the PRS composite score, 0 to 100 (higher is safer), set exactly when basis is 'prs'.
sovereign_spread() turns the rating or score into a default spread.
Known source issue: in the 2026-07-01 edition Barbados's Moody's rating is typed as C in his file (B2 in 2026-04-01),
so it carries Belarus's values: a default spread of 0.1750, crp 0.2720 and erp 0.3140.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from arora_data import _damodaran


class SovereignRating(_damodaran.CountryRow):
    basis: str
    moodys_rating: str | None
    prs_score: Decimal | None


async def sovereign_rating(country: str | None = None, on: dt.date | None = None,
                           revision: int | None = None) -> list[SovereignRating]:
    """Sovereign ratings from the Damodaran edition in force on on (default: today in US/Eastern), ordered by
    entity_code. Source: Aswath Damodaran, NYU Stern.

    country is an ISO alpha-3 code, AE-AZ, AE-RK or AE-SH for the emirates, or a name from that edition (any case);
    None gives every country. revision defaults to the edition's newest (damodaran_editions() lists them). An unknown
    country or revision, or a date before the first edition, raises ValueError.
    """
    rows = await _damodaran._rows(country, on, revision)
    return [SovereignRating(**r.model_dump(include=set(SovereignRating.model_fields))) for r in rows]
