"""Sovereign default spreads: the extra yield each government's debt pays over a riskless benchmark for its default
risk. Source: Aswath Damodaran, NYU Stern, via macro.damodaran_country_risk.

Each row has his two measures, as he published them and never recomputed, in fractions (0.0039 is 0.39%):
- default_spread: from the country's Moody's rating, through his table of spreads by rating (0 for Aaa); for
  countries without a rating (basis 'prs', sovereign_rating()) he derives it from their PRS score. Never NULL.
- cds_spread_net: the 10-year sovereign credit default swap spread, the market's yearly price of insuring the
  country's debt, net of the edition's benchmark: the US's through the 2025-01-01 edition, Switzerland's from
  2025-07-01 (damodaran_editions()' cds_benchmark). Floored at 0, never negative. NULL where no CDS trades,
  including every PRS country.
country_risk_premium() scales each by the edition's volatility multiplier.
Known source issue: in the 2026-07-01 edition Barbados's Moody's rating is typed as C in his file, so it carries
Belarus's values (default_spread 0.1750).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from arora_data import _damodaran


class SovereignSpread(_damodaran.CountryRow):
    default_spread: Decimal | None
    cds_spread_net: Decimal | None


async def sovereign_spread(country: str | None = None, on: dt.date | None = None,
                           revision: int | None = None) -> list[SovereignSpread]:
    """Sovereign default spreads from the Damodaran edition in force on on (default: today in US/Eastern), ordered
    by entity_code. Source: Aswath Damodaran, NYU Stern.

    country is an ISO alpha-3 code, AE-AZ, AE-RK or AE-SH for the emirates, or a name from that edition (any case);
    None gives every country. revision defaults to the edition's newest (damodaran_editions() lists them). An unknown
    country or revision, or a date before the first edition, raises ValueError.
    """
    rows = await _damodaran._rows(country, on, revision)
    return [SovereignSpread(**r.model_dump(include=set(SovereignSpread.model_fields))) for r in rows]
