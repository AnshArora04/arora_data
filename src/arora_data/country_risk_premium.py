"""Country risk premiums: the extra equity premium Damodaran adds for investing in each country. Source: Aswath
Damodaran, NYU Stern, via macro.damodaran_country_risk.

Each row has his two estimates, as he published them and never recomputed, in fractions (0.0061 is 0.61%):
- crp: from the country's rating. Its default spread (sovereign_spread()) times the edition's equity-to-bond
  volatility multiplier (damodaran_editions()). The US is the exception: its crp is the raw Aa1 spread, unscaled.
  Countries without a Moody's rating get a spread from their PRS score (sovereign_rating()).
- cds_crp: from the market. The 10-year sovereign CDS spread, net of the edition's benchmark (Switzerland in 2026),
  times the same multiplier. NULL where no CDS trades.
Known source issue: in the 2026-07-01 edition Barbados's Moody's rating is typed as C in his file, so it carries
Belarus's values (crp 0.2720). Rows are stored as he published them.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from arora_data import _damodaran


class CountryRiskPremium(_damodaran.CountryRow):
    crp: Decimal | None
    cds_crp: Decimal | None


async def country_risk_premium(country: str | None = None, on: dt.date | None = None,
                               revision: int | None = None) -> list[CountryRiskPremium]:
    """Country risk premiums from the Damodaran edition in force on on (default: today in US/Eastern), ordered by
    entity_code. Source: Aswath Damodaran, NYU Stern.

    country is an ISO alpha-3 code, AE-AZ, AE-RK or AE-SH for the emirates, or a name from that edition (any case);
    None gives every country. revision defaults to the edition's newest (damodaran_editions() lists them). An unknown
    country or revision, or a date before the first edition, raises ValueError.
    """
    rows = await _damodaran._rows(country, on, revision)
    return [CountryRiskPremium(**r.model_dump(include=set(CountryRiskPremium.model_fields))) for r in rows]
