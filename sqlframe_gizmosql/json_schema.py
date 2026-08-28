"""Spark-compatible JSON schema inference, done in streaming/constant memory.

Apache Spark's ``spark.read.json()`` infers one schema for a set of JSON
records with three simple rules (``JsonInferSchema`` in Spark):

1. Objects are merged field-by-field: the result has the union of every field
   ever seen, at every nesting level.
2. When the *same* field is seen with two irreconcilable types (a string in
   one record, an object in the next; an array of numbers here, an array of
   objects there) the field degrades to ``StringType`` — the raw JSON text.
   Integers and floats reconcile to ``double``.
3. Struct fields are sorted by name.

This module reproduces those rules over a stream of documents, so the schema
is built one record at a time with ``json.loads`` — no columnar
materialization, no pyarrow schema unification — and memory stays flat
regardless of file size.

It then adds the one deviation that keeps document-shaped data (MongoDB
exports, EHR forms, ...) tractable on this engine: a **nesting budget**. Spark
happily produces a struct with hundreds of thousands of leaf fields for such
data because its schema lives in the driver and never crosses a wire. Here it
would have to be expressed as a DuckDB ``STRUCT`` type, cross Arrow Flight as
a schema message (capped at 16 MiB per gRPC message by default), and be
re-parsed by sqlglot on every DataFrame operation — none of which survives a
million-leaf struct. So any nested subtree whose field count exceeds
``max_nested_fields`` collapses to a single ``JSON`` column (the subtree's
raw text, still fully queryable with DuckDB's JSON functions), exactly the
way Spark itself falls back to a string on a type conflict. Top-level columns
are never collapsed: the DataFrame always has one column per top-level key,
which is what Spark-derived pipelines key off.

The inferred schema is emitted as the *structure* argument of DuckDB's
``from_json(json, structure)``, which is lenient in the same way Spark is:
missing keys become NULL, a mismatch against a ``VARCHAR`` target yields the
raw JSON text rather than an error.
"""

from __future__ import annotations

import json
import random
import typing as t

from sqlglot import exp

# Inferred type representation:
#   None                         -> nothing but JSON nulls seen yet (Spark: NullType,
#                                   which canonicalizes to StringType)
#   "BIGINT" | "DOUBLE" | "BOOLEAN" | "VARCHAR" | "JSON"
#   ("array", elem)              -> Spark ArrayType
#   ("struct", {name: type...})  -> Spark StructType
InferredType = t.Union[None, str, t.Tuple[str, t.Any]]

_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1

#: Default nesting budget: a nested struct/array subtree with more than this
#: many fields (counted recursively) collapses to a single JSON column.
DEFAULT_MAX_NESTED_FIELDS = 1000


def infer_value_type(value: t.Any) -> InferredType:
    """Infer the type of one decoded JSON value, Spark-style."""
    if value is None:
        return None
    # bool is a subclass of int in Python; check it first.
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        # Spark widens integers outside the long range to DecimalType; DOUBLE is
        # the closest thing DuckDB's from_json can target without a precision.
        return "BIGINT" if _INT64_MIN <= value <= _INT64_MAX else "DOUBLE"
    if isinstance(value, float):
        return "DOUBLE"
    if isinstance(value, str):
        return "VARCHAR"
    if isinstance(value, list):
        element: InferredType = None
        for item in value:
            element = merge_types(element, infer_value_type(item))
        return ("array", element)
    if isinstance(value, dict):
        return ("struct", {key: infer_value_type(item) for key, item in value.items()})
    raise TypeError(f"Unsupported JSON value type: {type(value).__name__}")


def merge_types(left: InferredType, right: InferredType) -> InferredType:
    """Merge two inferred types the way Spark's ``compatibleType`` does."""
    if left is None:
        return right
    if right is None:
        return left
    if left == right:
        return left
    left_kind = left[0] if isinstance(left, tuple) else left
    right_kind = right[0] if isinstance(right, tuple) else right
    if left_kind == "struct" and right_kind == "struct":
        merged = dict(left[1])  # type: ignore[index]
        for name, right_type in right[1].items():  # type: ignore[index]
            merged[name] = merge_types(merged.get(name), right_type)
        return ("struct", merged)
    if left_kind == "array" and right_kind == "array":
        return ("array", merge_types(left[1], right[1]))  # type: ignore[index]
    if {left_kind, right_kind} == {"BIGINT", "DOUBLE"}:
        return "DOUBLE"
    # Anything else is a conflict: Spark falls back to StringType, i.e. the raw
    # JSON text of whatever the value is.
    return "VARCHAR"


def count_fields(inferred: InferredType) -> int:
    """Number of leaf fields in a subtree — the "size" the nesting budget
    is measured against."""
    if inferred is None or isinstance(inferred, str):
        return 1
    if inferred[0] == "array":
        return count_fields(inferred[1])
    return sum(count_fields(child) for child in inferred[1].values()) or 1


def apply_nesting_budget(inferred: InferredType, max_nested_fields: t.Optional[int]) -> InferredType:
    """Collapse nested subtrees wider than ``max_nested_fields`` to JSON.

    ``inferred`` is the top-level (struct) type; its own fields are never
    collapsed, only what hangs below them. ``None`` disables the budget.
    """
    if max_nested_fields is None or not isinstance(inferred, tuple):
        return inferred

    def _collapse(node: InferredType) -> InferredType:
        if node is None or isinstance(node, str):
            return node
        if count_fields(node) > max_nested_fields:
            return "JSON"
        if node[0] == "array":
            return ("array", _collapse(node[1]))
        return ("struct", {name: _collapse(child) for name, child in node[1].items()})

    if inferred[0] == "struct":
        return ("struct", {name: _collapse(child) for name, child in inferred[1].items()})
    return _collapse(inferred)


def to_from_json_structure(inferred: InferredType) -> t.Any:
    """Convert an inferred type to DuckDB's ``from_json`` structure object
    (nested dicts/lists of type-name strings), with struct fields sorted by
    name like Spark."""
    if inferred is None:
        # Only nulls seen: Spark's NullType canonicalizes to StringType.
        return "VARCHAR"
    if isinstance(inferred, str):
        return inferred
    if inferred[0] == "array":
        return [to_from_json_structure(inferred[1])]
    return {name: to_from_json_structure(inferred[1][name]) for name in sorted(inferred[1])}


def datatype_to_from_json_structure(data_type: exp.DataType) -> t.Any:
    """Convert a sqlglot DataType (from a user-supplied schema) to a
    ``from_json`` structure, preserving the user's field order."""
    if data_type.this == exp.DataType.Type.STRUCT:
        structure: t.Dict[str, t.Any] = {}
        for column_def in data_type.expressions:
            structure[column_def.this.name] = datatype_to_from_json_structure(column_def.args["kind"])
        return structure
    if data_type.this in (exp.DataType.Type.ARRAY, exp.DataType.Type.LIST):
        (element,) = data_type.expressions
        return [datatype_to_from_json_structure(element)]
    return data_type.sql(dialect="duckdb")


def structure_to_sql_literal(structure: t.Any) -> str:
    """Render a structure as a single-quoted SQL string literal."""
    return "'" + json.dumps(structure, separators=(",", ":")).replace("'", "''") + "'"


class JsonSchemaInferrer:
    """Accumulates a Spark-style schema over a stream of JSON documents.

    Parameters
    ----------
    sampling_ratio : float
        Fraction of documents to look at, like Spark's ``samplingRatio``
        option (1.0 = all). Sampling is deterministic for a given input order.
    max_nested_fields : int or None
        Nesting budget applied by :meth:`structure`; see
        :func:`apply_nesting_budget`.
    """

    def __init__(
        self,
        sampling_ratio: float = 1.0,
        max_nested_fields: t.Optional[int] = DEFAULT_MAX_NESTED_FIELDS,
    ) -> None:
        if not 0.0 < sampling_ratio <= 1.0:
            raise ValueError(f"samplingRatio must be in (0, 1], got {sampling_ratio}")
        if max_nested_fields is not None and max_nested_fields < 1:
            raise ValueError(f"maxNestedFields must be >= 1 (or unset), got {max_nested_fields}")
        self.sampling_ratio = sampling_ratio
        self.max_nested_fields = max_nested_fields
        self._random = random.Random(0)
        self._schema: InferredType = None
        self.documents_seen = 0
        self.documents_sampled = 0

    def observe(self, document: str) -> None:
        """Fold one raw JSON document (text) into the running schema."""
        self.documents_seen += 1
        if self.sampling_ratio < 1.0 and self._random.random() >= self.sampling_ratio:
            return
        self.documents_sampled += 1
        self.observe_value(value=json.loads(document))

    def observe_value(self, value: t.Any) -> None:
        """Fold one already-decoded JSON value into the running schema."""
        self._schema = merge_types(self._schema, infer_value_type(value))

    @property
    def inferred(self) -> InferredType:
        """The raw merged type, before the nesting budget."""
        return self._schema

    def top_level_fields(self) -> t.List[str]:
        """Sorted top-level column names (empty if no object was seen)."""
        if isinstance(self._schema, tuple) and self._schema[0] == "struct":
            return sorted(self._schema[1])
        return []

    def structure(self) -> t.Dict[str, t.Any]:
        """The ``from_json`` structure for the inferred schema, after the
        nesting budget. Raises if no top-level object fields were seen."""
        budgeted = apply_nesting_budget(inferred=self._schema, max_nested_fields=self.max_nested_fields)
        if not (isinstance(budgeted, tuple) and budgeted[0] == "struct" and budgeted[1]):
            raise ValueError(
                "Could not infer any columns: no JSON object with at least one key was seen "
                f"in the {self.documents_sampled} sampled document(s)."
            )
        return to_from_json_structure(budgeted)

    def collapsed_fields(self) -> t.List[str]:
        """Dotted paths of the subtrees the nesting budget collapsed to JSON —
        for logging, so users can see what did not get expanded."""
        collapsed: t.List[str] = []

        def _walk(original: InferredType, budgeted: t.Any, path: str) -> None:
            if isinstance(original, tuple):
                if budgeted == "JSON" and original[0] in ("struct", "array"):
                    collapsed.append(path)
                    return
                if original[0] == "array" and isinstance(budgeted, list):
                    _walk(original[1], budgeted[0], path + "[]")
                elif original[0] == "struct" and isinstance(budgeted, dict):
                    for name, child in original[1].items():
                        _walk(child, budgeted.get(name), f"{path}.{name}" if path else name)

        try:
            _walk(self._schema, self.structure(), "")
        except ValueError:
            pass
        return collapsed
