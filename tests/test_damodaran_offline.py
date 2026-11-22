"""The shared Damodaran read: edition in force, its revision, and one country, with the database faked."""
import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from arora_data import _damodaran as dm, damodaran_editions as de

JUL25, JAN26, APR26 = date(2025, 7, 1), date(2026, 1, 1), date(2026, 4, 1)


def edition(edition_date, revision, seen, latest):
    return de.CrpEdition(edition_date=edition_date, revision=revision, file_name="ctryprem.xlsx",
                         first_seen_at=datetime(*seen, tzinfo=timezone.utc), mature_erp=Decimal("0.0423"),
                         us_erp=None, vol_multiplier=None, cds_benchmark=None, editor_note=None, latest=latest)


# January's newest revision is 2, first seen last, though 3 has the higher number.
EDITIONS = [edition(JUL25, 1, (2025, 7, 2), True),
            edition(JAN26, 1, (2026, 1, 7), False), edition(JAN26, 3, (2026, 1, 20), False),
            edition(JAN26, 2, (2026, 2, 17), True),
            edition(APR26, 1, (2026, 4, 2), True)]


def row(code, name, crp, edition_date=JAN26, revision=2):
    return dm._CountryRisk(edition_date=edition_date, revision=revision, entity_code=code, published_name=name,
                           entity_type="subnational" if "-" in code else "country", region="Region",
                           moodys_rating="Aa1", basis="rating", prs_score=None, default_spread=Decimal(crp),
                           cds_spread_net=None, crp=Decimal(crp), cds_crp=None, erp=Decimal("0.0423") + Decimal(crp),
                           cds_erp=None)


ROWS = {(JAN26, 2): [row("AE-AZ", "Abu Dhabi", "0.0061095009"), row("USA", "United States", "0.0023336792")],
        (JAN26, 1): [row("USA", "United States", "0.0024", revision=1)],
        (APR26, 1): [row("USA", "United States", "0.0026", APR26, 1)]}


@pytest.fixture
def db(monkeypatch):
    reads = []

    async def editions():
        reads.append("editions")
        return EDITIONS

    async def fetch_rows(edition_date, revision):
        reads.append((edition_date, revision))
        return ROWS.get((edition_date, revision), [])

    monkeypatch.setattr(de, "damodaran_editions", editions)
    monkeypatch.setattr(dm, "_fetch_rows", fetch_rows)
    monkeypatch.setattr(dm, "today_et", lambda: date(2026, 3, 31))
    return reads


def run(country=None, on=None, revision=None):
    return asyncio.run(dm._rows(country, on, revision))


def test_by_default_the_edition_in_force_today_eastern_at_its_newest_revision_by_first_seen(db):
    assert run() == ROWS[(JAN26, 2)]
    assert db == ["editions", (JAN26, 2)]


@pytest.mark.parametrize("on, read", [(date(2026, 3, 31), (JAN26, 2)), (APR26, (APR26, 1)), (date(2025, 12, 31), (JUL25, 1)),
                                      (date(2030, 1, 1), (APR26, 1))])
def test_on_picks_the_latest_edition_dated_on_or_before_it(db, on, read):
    run(on=on)
    assert db[-1] == read


def test_a_given_revision_is_read_and_an_unknown_one_raises_before_any_row_read(db):
    assert run("USA", revision=1) == ROWS[(JAN26, 1)]
    with pytest.raises(ValueError, match=r"Damodaran's 2026-01-01 edition has no revision 4: it has 1, 3, 2$"):
        run(revision=4)
    assert db == ["editions", (JAN26, 1), "editions"]


def test_a_date_before_the_first_edition_raises_before_any_row_read(db):
    with pytest.raises(ValueError, match=r"no Damodaran edition on or before 2025-06-30: the first is 2025-07-01$"):
        run(on=date(2025, 6, 30))
    assert db == ["editions"]


def test_no_stored_editions_raises(db, monkeypatch):
    async def none():
        return []

    monkeypatch.setattr(de, "damodaran_editions", none)
    with pytest.raises(ValueError, match="no Damodaran editions are stored"):
        run()


def test_a_code_or_name_in_any_case_and_spacing_is_the_one_row_values_unchanged(db):
    usa = [ROWS[(JAN26, 2)][1]]
    assert run("USA") == run(" usa ") == run("united   STATES") == usa
    assert usa[0].crp == Decimal("0.0023336792")
    assert run("ae-az") == run("abu dhabi") == [ROWS[(JAN26, 2)][0]]


@pytest.mark.parametrize("bad", ["South Korea", "US", "", "Region", 5])
def test_an_unknown_country_raises_naming_the_edition(db, bad):
    with pytest.raises(ValueError, match=r"is not a stored country in Damodaran's 2026-01-01 edition: use an ISO alpha-3"):
        run(bad)
