"""sovereign_rating(): the shared read's rows, cut to their ratings, with the read faked."""
import asyncio
from datetime import date
from decimal import Decimal

import pytest

from arora_data import _damodaran as dm, sovereign_rating as sr
from test_damodaran_offline import row


@pytest.fixture
def reads(monkeypatch):
    calls = []

    async def rows(country, on, revision):
        calls.append((country, on, revision))
        return [row("AE-AZ", "Abu Dhabi", "0.0061095009").model_copy(update={"moodys_rating": "Aa2"}),
                row("GIN", "Guinea", "0.1208").model_copy(
                    update={"basis": "prs", "moodys_rating": None, "prs_score": Decimal("48.50")})]

    monkeypatch.setattr(dm, "_rows", rows)
    return calls


def test_each_row_is_its_edition_country_basis_rating_and_score_unchanged(reads):
    served = asyncio.run(sr.sovereign_rating())
    assert [r.model_dump() for r in served] == [
        {"edition_date": date(2026, 1, 1), "revision": 2, "entity_code": code, "published_name": name,
         "entity_type": kind, "region": "Region", "basis": basis, "moodys_rating": rating, "prs_score": score}
        for code, name, kind, basis, rating, score in [
            ("AE-AZ", "Abu Dhabi", "subnational", "rating", "Aa2", None),
            ("GIN", "Guinea", "country", "prs", None, Decimal("48.50"))]]
    assert reads == [(None, None, None)]


def test_country_on_and_revision_reach_the_shared_read(reads):
    asyncio.run(sr.sovereign_rating("usa", date(2026, 3, 31), 1))
    asyncio.run(sr.sovereign_rating(on=date(2025, 1, 1)))
    assert reads == [("usa", date(2026, 3, 31), 1), (None, date(2025, 1, 1), None)]
