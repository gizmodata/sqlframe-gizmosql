"""Unit tests for Spark-compatible streaming JSON schema inference."""

import json

import pytest
from sqlglot import exp

from sqlframe_gizmosql.json_schema import (
    JsonSchemaInferrer,
    apply_nesting_budget,
    count_fields,
    datatype_to_from_json_structure,
    infer_value_type,
    merge_types,
    structure_to_sql_literal,
    to_from_json_structure,
)


def infer(*documents, **kwargs):
    inferrer = JsonSchemaInferrer(**kwargs)
    for document in documents:
        inferrer.observe(document=json.dumps(document))
    return inferrer


class TestInferValueType:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, None),
            (True, "BOOLEAN"),
            (1, "BIGINT"),
            (2**63, "DOUBLE"),
            (1.5, "DOUBLE"),
            ("x", "VARCHAR"),
            ([1, 2], ("array", "BIGINT")),
            ([], ("array", None)),
            ({"a": 1}, ("struct", {"a": "BIGINT"})),
        ],
    )
    def test_scalars_and_containers(self, value, expected):
        assert infer_value_type(value) == expected


class TestMergeTypes:
    def test_null_is_absorbed(self):
        assert merge_types(None, "BIGINT") == "BIGINT"
        assert merge_types("BIGINT", None) == "BIGINT"

    def test_int_and_float_widen_to_double(self):
        assert merge_types("BIGINT", "DOUBLE") == "DOUBLE"

    def test_conflict_falls_back_to_string_like_spark(self):
        assert merge_types("BIGINT", "VARCHAR") == "VARCHAR"
        assert merge_types("BOOLEAN", "BIGINT") == "VARCHAR"
        assert merge_types(("struct", {"a": "BIGINT"}), "VARCHAR") == "VARCHAR"
        assert merge_types(("array", "BIGINT"), ("struct", {})) == "VARCHAR"

    def test_structs_union_fields_recursively(self):
        merged = merge_types(
            ("struct", {"a": "BIGINT", "n": ("struct", {"x": "VARCHAR"})}),
            ("struct", {"b": "BOOLEAN", "n": ("struct", {"y": "DOUBLE"})}),
        )
        assert merged == ("struct", {"a": "BIGINT", "n": ("struct", {"x": "VARCHAR", "y": "DOUBLE"}), "b": "BOOLEAN"})

    def test_array_elements_merge(self):
        assert merge_types(("array", "BIGINT"), ("array", None)) == ("array", "BIGINT")
        assert merge_types(("array", "BIGINT"), ("array", "VARCHAR")) == ("array", "VARCHAR")


class TestNestingBudget:
    def test_count_fields(self):
        assert count_fields("BIGINT") == 1
        assert count_fields(("array", ("struct", {"a": None, "b": "BIGINT"}))) == 2
        assert count_fields(("struct", {})) == 1

    def test_wide_subtree_collapses_to_json_but_top_level_never_does(self):
        wide = ("struct", {f"f{i}": "BIGINT" for i in range(5)})
        inferred = ("struct", {"small": ("struct", {"a": "BIGINT"}), "big": wide, "bigs": ("array", wide)})

        budgeted = apply_nesting_budget(inferred=inferred, max_nested_fields=3)

        assert budgeted == ("struct", {"small": ("struct", {"a": "BIGINT"}), "big": "JSON", "bigs": "JSON"})
        # Even the top level has > 3 fields in total; it is not collapsed.
        assert apply_nesting_budget(inferred=wide, max_nested_fields=3) == wide

    def test_none_disables_budget(self):
        wide = ("struct", {f"f{i}": "BIGINT" for i in range(5)})
        assert apply_nesting_budget(inferred=("struct", {"big": wide}), max_nested_fields=None) == (
            "struct",
            {"big": wide},
        )


class TestStructure:
    def test_fields_sorted_and_nulls_become_varchar(self):
        assert to_from_json_structure(("struct", {"b": None, "a": ("array", None)})) == {
            "a": ["VARCHAR"],
            "b": "VARCHAR",
        }

    def test_sql_literal_escapes_quotes(self):
        assert structure_to_sql_literal({"it's": "VARCHAR"}) == "'{\"it''s\":\"VARCHAR\"}'"

    def test_from_spark_datatype(self):
        data_type = exp.DataType.build("STRUCT<a: INT, tags: ARRAY<STRING>, n: STRUCT<x: DOUBLE>>", dialect="spark")
        assert datatype_to_from_json_structure(data_type) == {"a": "INT", "tags": ["TEXT"], "n": {"x": "DOUBLE"}}


class TestJsonSchemaInferrer:
    def test_end_to_end_spark_semantics(self):
        inferrer = infer(
            {"id": 1, "meta": {"_oid": "abc"}, "tags": [{"k": 1}], "flag": True},
            {"id": 2.5, "meta": {"_date": "2024"}, "tags": "oops", "extra": None},
        )
        assert inferrer.structure() == {
            "extra": "VARCHAR",
            "flag": "BOOLEAN",
            "id": "DOUBLE",
            "meta": {"_date": "VARCHAR", "_oid": "VARCHAR"},
            "tags": "VARCHAR",
        }
        assert inferrer.top_level_fields() == ["extra", "flag", "id", "meta", "tags"]
        assert inferrer.collapsed_fields() == []

    def test_collapsed_fields_are_reported(self):
        inferrer = infer({"ok": {"a": 1}, "huge": {"deep": [{f"f{i}": i for i in range(50)}]}}, max_nested_fields=10)
        assert inferrer.structure()["huge"] == "JSON"
        assert inferrer.structure()["ok"] == {"a": "BIGINT"}
        assert inferrer.collapsed_fields() == ["huge"]

    def test_sampling_ratio_is_deterministic_and_skips_documents(self):
        docs = [{"i": n} for n in range(200)]
        a = infer(*docs, sampling_ratio=0.25)
        b = infer(*docs, sampling_ratio=0.25)
        assert a.documents_seen == 200
        assert 0 < a.documents_sampled < 200
        assert a.documents_sampled == b.documents_sampled
        assert a.structure() == {"i": "BIGINT"}

    def test_no_object_fields_raises(self):
        with pytest.raises(ValueError, match="Could not infer any columns"):
            infer({}, {}).structure()
        with pytest.raises(ValueError, match="Could not infer any columns"):
            infer(1, "x").structure()

    def test_invalid_parameters(self):
        with pytest.raises(ValueError, match="samplingRatio"):
            JsonSchemaInferrer(sampling_ratio=0)
        with pytest.raises(ValueError, match="maxNestedFields"):
            JsonSchemaInferrer(max_nested_fields=0)
