from datetime import date, datetime, timezone
from decimal import Decimal

import polars as pl
import pytest
from pydantic import BaseModel

from arora_data.catalog import SeriesInfo
from arora_data.frames import to_df


class Price(BaseModel):
    ticker: str
    close: Decimal | None
    on: date | None
    volume: int | None


def price(close):
    return Price(ticker="X", close=close, on=date(2026, 10, 30), volume=1)


def test_decimals_stay_exact_across_scales_and_sizes():
    closes = ["0.1", "1234567890123456789012.123456", "1.5"]
    df = to_df([price(Decimal(c)) for c in closes], Price)
    assert isinstance(df.schema["close"], pl.Decimal)
    assert df["close"].to_list() == [Decimal(c) for c in closes]


def test_decimal_sum_has_no_float_error():
    df = to_df([price(Decimal("0.1")), price(Decimal("0.2"))], Price)
    assert df["close"].sum() == Decimal("0.3")


def test_empty_result_keeps_typed_columns():
    df = to_df([], Price)
    assert df.shape == (0, 4)
    assert df.schema["ticker"] == pl.String and df.schema["on"] == pl.Date and df.schema["volume"] == pl.Int64
    assert isinstance(df.schema["close"], pl.Decimal)


def test_all_blank_columns_keep_their_types():
    df = to_df([Price(ticker="X", close=None, on=None, volume=None)], Price)
    assert isinstance(df.schema["close"], pl.Decimal)
    assert df.schema["on"] == pl.Date and df.schema["volume"] == pl.Int64


def test_catalog_rows():
    rows = [SeriesInfo(key="AAA", dataset=None, description="Yield", latest_date=date(2026, 9, 1))]
    df = to_df(rows, SeriesInfo)
    assert df.columns == ["key", "dataset", "description", "latest_date"]
    assert df.schema["dataset"] == pl.String and df["latest_date"].to_list() == [date(2026, 9, 1)]


def test_literal_columns_are_strings():
    from arora_data.forex import FxBar

    row = FxBar(pair="CADUSD", date=date(2026, 11, 6), route="inverted", open=None, high=None, low=None,
                close=Decimal("0.71"), volume=None, vwap=None)
    df = to_df([row], FxBar)
    assert df.schema["route"] == pl.String and df["route"].to_list() == ["inverted"]


def test_values_too_long_for_polars_raise_instead_of_turning_null():
    rows = [price(Decimal("0.000000000000000000000000000001")), price(Decimal("123456789012"))]
    with pytest.raises(ValueError, match="close"):
        to_df(rows, Price)


def test_datetimes_keep_their_instant_shown_in_eastern():
    from arora_data.damodaran_editions import CrpEdition

    seen = datetime(2026, 11, 13, 3, 49, 5, tzinfo=timezone.utc)
    row = CrpEdition(edition_date=date(2026, 7, 1), revision=1, file_name="ctrypremJuly26.xlsx", first_seen_at=seen,
                     mature_erp=Decimal("0.042"), us_erp=None, vol_multiplier=None, cds_benchmark=None,
                     editor_note=None, latest=True)
    df = to_df([row], CrpEdition)
    assert df.schema["first_seen_at"] == pl.Datetime("us", "America/New_York")
    assert df["first_seen_at"][0] == seen and df["first_seen_at"].dt.date()[0] == date(2026, 11, 12)
