"""sovereign_spread(): the shared read's rows, cut to their spreads, with the read faked."""
import asyncio
from datetime import date
from decimal import Decimal

import pytest

from arora_data import _damodaran as dm, sovereign_spread as ss
from test_damodaran_offline import row


@pytest.fixture
def reads(monkeypatch):
    calls = []

    async def rows(country, on, revision):
        calls.append((country, on, revision))
        return [row("AE-AZ", "Abu Dhabi", "0.0061095009").model_copy(update={"cds_spread_net": Decimal("0.0052")}),
                row("USA", "United States", "0.0023336792")]

    monkeypatch.setattr(dm, "_rows", rows)
    return calls


def test_each_row_is_its_edition_country_and_both_spreads_unchanged(reads):
    served = asyncio.run(ss.sovereign_spread())
    assert [r.model_dump() for r in served] == [
        {"edition_date": date(2026, 1, 1), "revision": 2, "entity_code": code, "published_name": name,
         "entity_type": kind, "region": "Region", "default_spread": Decimal(spread), "cds_spread_net": cds}
        for code, name, kind, spread, cds in [("AE-AZ", "Abu Dhabi", "subnational", "0.0061095009", Decimal("0.0052")),
                                              ("USA", "United States", "country", "0.0023336792", None)]]
    assert reads == [(None, None, None)]


def test_country_on_and_revision_reach_the_shared_read(reads):
    asyncio.run(ss.sovereign_spread("usa", date(2026, 3, 31), 1))
    asyncio.run(ss.sovereign_spread(on=date(2025, 1, 1)))
    assert reads == [("usa", date(2026, 3, 31), 1), (None, date(2025, 1, 1), None)]
