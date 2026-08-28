from __future__ import annotations

import inspect
import sys

import sqlframe.base.functions  # noqa

module = sys.modules["sqlframe.base.functions"]
globals().update(
    {
        name: func
        for name, func in inspect.getmembers(module, inspect.isfunction)
        if hasattr(func, "unsupported_engines")
        and "duckdb" not in func.unsupported_engines
        and "*" not in func.unsupported_engines
    }
)


def get_json_object(col, path: str):
    """Spark's get_json_object returns a *string*: bare scalars (no JSON
    quotes) and the JSON text of objects/arrays. That is DuckDB's ``->>``
    (json_extract_string), not sqlframe's default ``->`` (json_extract),
    which returns JSON-typed values with scalar strings still quoted."""
    from sqlframe.base.column import Column
    from sqlglot import exp

    return Column.invoke_expression_over_column(col, exp.JSONExtractScalar, expression=exp.Literal.string(path))


get_json_object.unsupported_engines = []  # type: ignore[attr-defined]
