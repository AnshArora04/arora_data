"""Polars dataframes from package results. Needs the `dataframe` extra; nothing else imports this."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from types import NoneType
from typing import Any, get_args

import polars as pl
from pydantic import BaseModel

# Decimal is left for Polars to infer, because each column has its own scale.
_DTYPES: dict[Any, Any] = {str: pl.String, int: pl.Int64, bool: pl.Boolean, date: pl.Date, Decimal: None}


def _dtype(annotation: Any) -> Any:
    args = [a for a in get_args(annotation) if a is not NoneType]
    return _DTYPES[args[0] if args else annotation]


def to_df(rows: Sequence[BaseModel], model: type[BaseModel]) -> pl.DataFrame:
    """Rows as a dataframe. The model sets the columns, so empty results keep them too."""
    schema = {name: _dtype(field.annotation) for name, field in model.model_fields.items()}
    df = pl.DataFrame([r.model_dump() for r in rows], schema=schema, infer_schema_length=None)
    # A Decimal column with no values has nothing to infer from, so it would stay untyped.
    return df.cast({name: pl.Decimal(scale=0) for name, dtype in df.schema.items() if dtype == pl.Null})
