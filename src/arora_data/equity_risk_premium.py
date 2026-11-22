"""Total equity risk premiums: the premium over a riskless rate Damodaran estimates for investing in each country's
stocks. Source: Aswath Damodaran, NYU Stern, via macro.damodaran_country_risk.

Each row has his two estimates, as he published them and never recomputed, in fractions (0.0481 is 4.81%):
- erp: the edition's mature-market premium (damodaran_editions()) plus the rating-based crp (country_risk_premium()).
- cds_erp: the same mature premium plus the CDS-based cds_crp. NULL where no CDS trades.
Two exceptions in his files, served as published:
- the US from 2026: erp is his separate US premium (damodaran_editions()' us_erp, 0.0442 in July 2026), not the
  mature premium plus the US's raw spread;
- the 2017-01-05 edition's countries without a Moody's rating (basis 'prs'): erp is as his file has it and isn't
  the mature premium plus crp (Guinea: 0.2000, not 0.0569 + 0.1208).
Known source issue: in the 2026-07-01 edition Barbados's Moody's rating is typed as C in his file, so it carries
Belarus's values (erp 0.3140).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from arora_data import _damodaran


class EquityRiskPremium(_damodaran.CountryRow):
    erp: Decimal | None
    cds_erp: Decimal | None


async def equity_risk_premium(country: str | None = None, on: dt.date | None = None,
                              revision: int | None = None) -> list[EquityRiskPremium]:
    """Total equity risk premiums from the Damodaran edition in force on on (default: today in US/Eastern), ordered
    by entity_code. Source: Aswath Damodaran, NYU Stern.

    country is an ISO alpha-3 code, AE-AZ, AE-RK or AE-SH for the emirates, or a name from that edition (any case);
    None gives every country. revision defaults to the edition's newest (damodaran_editions() lists them). An unknown
    country or revision, or a date before the first edition, raises ValueError.
    """
    rows = await _damodaran._rows(country, on, revision)
    return [EquityRiskPremium(**r.model_dump(include=set(EquityRiskPremium.model_fields))) for r in rows]
