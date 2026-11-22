"""equity_risk_premium(): the shared read's rows, cut to their equity premiums, with the read faked."""
import asyncio
from datetime import date
from decimal import Decimal

import pytest

from arora_data import _damodaran as dm, equity_risk_premium as er
from test_damodaran_offline import row


@pytest.fixture
def reads(monkeypatch):
    calls = []

    async def rows(country, on, revision):
        calls.append((country, on, revision))
        return [row("AE-AZ", "Abu Dhabi", "0.0061095009"), row("USA", "United States", "0.0023336792")]

    monkeypatch.setattr(dm, "_rows", rows)
    return calls


def test_each_row_is_its_edition_country_and_both_premiums_unchanged(reads):
    served = asyncio.run(er.equity_risk_premium())
    assert [r.model_dump() for r in served] == [
        {"edition_date": date(2026, 1, 1), "revision": 2, "entity_code": code, "published_name": name,
         "entity_type": kind, "region": "Region", "erp": Decimal(erp), "cds_erp": None}
        for code, name, kind, erp in [("AE-AZ", "Abu Dhabi", "subnational", "0.0484095009"),
                                      ("USA", "United States", "country", "0.0446336792")]]
    assert reads == [(None, None, None)]


def test_country_on_and_revision_reach_the_shared_read(reads):
    asyncio.run(er.equity_risk_premium("usa", date(2026, 3, 31), 1))
    asyncio.run(er.equity_risk_premium(on=date(2025, 1, 1)))
    assert reads == [("usa", date(2026, 3, 31), 1), (None, date(2025, 1, 1), None)]
