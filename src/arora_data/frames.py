"""Polars dataframes from package results. Needs the `dataframe` extra; nothing else imports this."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from types import NoneType
from typing import Any, Literal, get_args, get_origin

import polars as pl
from pydantic import BaseModel

# Decimal is left for Polars to infer, because each column has its own scale.
# Datetimes are shown in US/Eastern, like every time the package serves.
_DTYPES: dict[Any, Any] = {str: pl.String, int: pl.Int64, bool: pl.Boolean, date: pl.Date,
                           datetime: pl.Datetime("us", "America/New_York"), Decimal: None}


def _dtype(annotation: Any) -> Any:
    if get_origin(annotation) is Literal:
        return pl.String
    args = [a for a in get_args(annotation) if a is not NoneType]
    return _DTYPES[args[0] if args else annotation]


def to_df(rows: Sequence[BaseModel], model: type[BaseModel]) -> pl.DataFrame:
    """Rows as a dataframe. The model sets the columns, so empty results keep them too."""
    schema = {name: _dtype(field.annotation) for name, field in model.model_fields.items()}
    dumped = [r.model_dump() for r in rows]
    df = pl.DataFrame(dumped, schema=schema, infer_schema_length=None)
    # Polars turns a Decimal that doesn't fit its column's 38 digits into null, without an error.
    for name in (n for n, dtype in schema.items() if dtype is None):
        if df[name].null_count() != sum(r[name] is None for r in dumped):
            raise ValueError(f"{name} has values too long for a Polars decimal (38 digits); round them first")
    # A Decimal column with no values has nothing to infer from, so it would stay untyped.
    return df.cast({name: pl.Decimal(scale=0) for name, dtype in df.schema.items() if dtype == pl.Null})
