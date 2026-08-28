"""Integration tests for the client-side bulk-ingest fast path behind
spark.read.json()/csv()/parquet() with client-local files."""

import json

import pyarrow as pa
import pyarrow.parquet as pa_parquet
import pytest

from sqlframe_gizmosql import functions as F


class TestReadLocalFiles:
    """spark.read.* against files that exist on the client filesystem should
    parse them locally with pyarrow and bulk-ingest into a session-scoped
    temporary table — no server-side filesystem access involved."""

    def test_read_json_local_file(self, session, tmp_path):
        f = tmp_path / "people.jsonl"
        f.write_text('{"id": 1, "name": "Alice"}\n{"id": 2, "name": "Bob"}\n')

        df = session.read.json(str(f))
        result = sorted(df.collect(), key=lambda row: row.id)

        assert len(result) == 2
        assert result[0].name == "Alice"

    def test_read_json_glob(self, session, tmp_path):
        (tmp_path / "part_1.jsonl").write_text('{"id": 1}\n')
        (tmp_path / "part_2.jsonl").write_text('{"id": 2}\n')

        df = session.read.json(str(tmp_path / "part_*.jsonl"))

        assert df.count() == 2

    def test_read_json_nested(self, session, tmp_path):
        f = tmp_path / "nested.jsonl"
        f.write_text('{"id": 1, "tags": ["a", "b"], "info": {"city": "NYC"}}\n')

        df = session.read.json(str(f))
        row = df.collect()[0]

        assert row.tags == ["a", "b"]

    def test_read_csv_local_file_with_header(self, session, tmp_path):
        f = tmp_path / "people.csv"
        f.write_text("id,name\n1,Alice\n2,Bob\n")

        df = session.read.csv(str(f), header=True)
        result = sorted(df.collect(), key=lambda row: row.id)

        assert len(result) == 2
        assert result[1].name == "Bob"

    def test_read_parquet_local_file(self, session, tmp_path):
        f = tmp_path / "people.parquet"
        pa_parquet.write_table(pa.table({"id": [1, 2, 3]}), f)

        df = session.read.parquet(str(f))

        assert df.count() == 3

    def test_read_with_schema_casts(self, session, tmp_path):
        f = tmp_path / "typed.jsonl"
        f.write_text('{"id": 1, "score": 9}\n')

        df = session.read.json(str(f), schema="id INT, score DOUBLE")
        row = df.collect()[0]

        assert row.id == 1
        assert isinstance(row.score, float)

    def test_read_load_api(self, session, tmp_path):
        f = tmp_path / "load.jsonl"
        f.write_text('{"id": 42}\n')

        df = session.read.load(path=str(f), format="json")

        assert df.collect()[0].id == 42

    def test_dataframe_operations_after_local_read(self, session, tmp_path):
        f = tmp_path / "ops.jsonl"
        f.write_text('{"grp": "a", "v": 1}\n{"grp": "a", "v": 2}\n{"grp": "b", "v": 3}\n')

        df = session.read.json(str(f))
        counts = {row.grp: row["count"] for row in df.groupBy("grp").count().collect()}

        assert counts == {"a": 2, "b": 1}

    def test_local_read_uses_temporary_table(self, session, tmp_path):
        """The materialized table must be temporary — not visible in the
        server's persistent catalog."""
        f = tmp_path / "tempcheck.jsonl"
        f.write_text('{"id": 1}\n')

        df = session.read.json(str(f))
        assert df.count() == 1

        rows = session.sql(
            "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'sqlframe_gizmosql_read_%' AND table_type = 'BASE TABLE'"
        ).collect()
        assert rows == []

    def test_json_document_mode(self, session, tmp_path):
        """jsonDocument=True ships raw document strings into a single JSON
        column with no client-side parsing — the route for document-shaped
        data whose typed schema explodes."""
        f = tmp_path / "forms.jsonl"
        f.write_text(
            '{"id": 1, "patient": {"name": "Alice", "scores": [9, 7]}}\n'
            '{"id": 2, "patient": {"name": "Bob"}, "extra_field_only_here": true}\n'
        )

        df = session.read.option("jsonDocument", True).json(str(f))

        assert df.columns == ["json"]
        assert df.count() == 2

        # The column must be queryable with DuckDB JSON functions server-side.
        df.createOrReplaceTempView("json_docs")
        rows = session.sql(
            "SELECT CAST(json_extract_string(json, '$.patient.name') AS VARCHAR) AS name FROM json_docs ORDER BY name"
        ).collect()
        assert [row.name for row in rows] == ["Alice", "Bob"]

    def test_json_document_mode_typed_extraction(self, session, tmp_path):
        """The documented follow-up: selectively type fields with the standard
        PySpark idiom — get_json_object + cast (translated to DuckDB's JSON
        functions by sqlglot)."""
        from sqlframe_gizmosql import functions as F

        f = tmp_path / "typed_docs.jsonl"
        f.write_text('{"id": 7, "patient": {"age": 66}}\n{"id": 8, "patient": {"age": 41}}\n')

        df = session.read.option("jsonDocument", True).json(str(f))
        typed = df.select(
            F.get_json_object(df["json"], "$.id").cast("int").alias("id"),
            F.get_json_object(df["json"], "$.patient.age").cast("int").alias("age"),
        ).where("age > 50")
        rows = typed.collect()

        assert len(rows) == 1
        assert rows[0].id == 7
        assert rows[0].age == 66

    def test_json_document_full_customer_flow(self, session, tmp_path):
        """The end-to-end documented pattern: jsonDocument read -> persist raw
        with saveAsTable -> typed CTAS via spark.sql().collect() -> plain SQL
        against the typed table. Exercises session.sql()'s self-healing table
        registration (tables created via raw SQL are unknown to sqlframe's
        qualifier until introspected)."""
        f = tmp_path / "flow_docs.jsonl"
        f.write_text(
            '{"_id": {"_oid": "a1"}, "finalized": true, "createdat": {"_date": "2026-01-01T10:00:00Z"}}\n'
            '{"_id": {"_oid": "b2"}, "finalized": false, "createdat": {"_date": "2026-01-02T11:00:00Z"}}\n'
        )

        df = session.read.option("jsonDocument", True).json(str(f))
        df.write.mode("overwrite").saveAsTable("flow_raw")

        session.sql(
            "CREATE OR REPLACE TABLE flow_typed AS "
            "SELECT get_json_object(json, '$._id._oid') AS form_id, "
            "CAST(get_json_object(json, '$.finalized') AS BOOLEAN) AS finalized, "
            "CAST(get_json_object(json, '$.createdat._date') AS TIMESTAMP) AS created_at, "
            "json AS raw "
            "FROM flow_raw"
        ).collect()

        rows = session.sql(
            "SELECT finalized, COUNT(*) AS n FROM flow_typed GROUP BY finalized ORDER BY finalized"
        ).collect()
        assert [(row.finalized, row.n) for row in rows] == [(False, 1), (True, 1)]
        assert session.table("flow_typed").where("finalized").count() == 1

    def test_json_document_mode_with_schema_raises(self, session, tmp_path):
        import pytest

        f = tmp_path / "docs.jsonl"
        f.write_text('{"id": 1}\n')

        with pytest.raises(ValueError, match="jsonDocument"):
            session.read.option("jsonDocument", True).json(str(f), schema="id INT")

    def test_json_document_mode_missing_path_raises(self, session, tmp_path):
        import pytest

        with pytest.raises(FileNotFoundError, match="jsonDocument"):
            session.read.option("jsonDocument", True).json(str(tmp_path / "nope.jsonl"))

    def test_server_side_read_opt_out(self, session, tmp_path):
        """serverSideRead=True must bypass the client-side path and generate a
        server-side read_json() (the test server shares this filesystem, so it
        still succeeds — the point is it goes through SQL)."""
        f = tmp_path / "serverside.jsonl"
        f.write_text('{"id": 7}\n')

        df = session.read.option("serverSideRead", True).json(str(f))

        assert df.collect()[0].id == 7

    def test_missing_local_path_falls_back_to_server_side(self, session, tmp_path):
        """A path that does not exist client-side falls back to server-side
        read_json() — here that also fails (shared filesystem), but with the
        server's error, proving the fallback engaged rather than a client
        FileNotFoundError."""
        import pytest

        with pytest.raises(Exception) as excinfo:
            session.read.json(str(tmp_path / "does_not_exist.jsonl")).collect()
        assert "does_not_exist" in str(excinfo.value)


class TestReadJsonSparkCompatibleSchema:
    """spark.read.json() must produce one typed column per top-level key with
    Spark's inference semantics, while staying loadable for document-shaped
    data by collapsing oversized nested subtrees to JSON columns."""

    def test_columns_expand_like_spark(self, session, tmp_path):
        f = tmp_path / "forms.jsonl"
        f.write_text(
            '{"_id": {"_oid": "a1"}, "createdat": {"_date": "2026-01-01"}, "finalized": true, "n": 1}\n'
            '{"_id": {"_oid": "b2"}, "finalized": false, "n": 2.5, "tags": ["x"]}\n'
        )

        df = session.read.json(str(f))

        assert df.columns == ["_id", "createdat", "finalized", "n", "tags"]
        field_types = {field.name: field.dataType.simpleString() for field in df.schema.fields}
        assert field_types == {
            "_id": "struct<_oid:string>",
            "createdat": "struct<_date:string>",
            "finalized": "boolean",
            "n": "double",
            "tags": "array<string>",
        }
        rows = sorted(df.collect(), key=lambda row: row.n)
        assert rows[0]._id._oid == "a1"
        assert rows[0].createdat._date == "2026-01-01"
        assert rows[1].createdat is None
        assert rows[1].tags == ["x"]
        assert df.where(df["finalized"]).select(df["_id"]["_oid"].alias("oid")).collect()[0].oid == "a1"

    def test_type_conflicts_fall_back_to_string_like_spark(self, session, tmp_path):
        f = tmp_path / "conflict.jsonl"
        f.write_text('{"id": 1, "v": {"k": 1}, "arr": [1]}\n{"id": 2, "v": "plain", "arr": [{"z": 1}]}\n')

        df = session.read.json(str(f))

        field_types = {field.name: field.dataType.simpleString() for field in df.schema.fields}
        assert field_types == {"arr": "array<string>", "id": "bigint", "v": "string"}
        rows = sorted(df.collect(), key=lambda row: row.id)
        assert rows[0].v == '{"k":1}'
        assert rows[1].v == "plain"
        assert rows[1].arr == ['{"z":1}']

    def test_oversized_nested_subtree_collapses_to_json(self, session, tmp_path):
        f = tmp_path / "wide.jsonl"
        wide = {f"field_{i}": i for i in range(30)}
        f.write_text(json.dumps({"id": 1, "small": {"a": 1}, "details": {"sections": [wide, wide]}}) + "\n")

        df = session.read.option("maxNestedFields", 10).json(str(f))

        field_types = {field.name: field.dataType.simpleString() for field in df.schema.fields}
        assert field_types["small"] == "struct<a:bigint>"
        assert field_types["details"] == "string"  # DuckDB JSON surfaces as a string type
        row = df.select(F.get_json_object(df["details"], "$.sections[1].field_29").alias("v")).collect()[0]
        assert row.v == "29"

    def test_budget_disabled_expands_everything(self, session, tmp_path):
        f = tmp_path / "deep.jsonl"
        f.write_text(json.dumps({"d": {"s": [{f"f{i}": i for i in range(30)}]}}) + "\n")

        df = session.read.option("maxNestedFields", None).json(str(f))

        assert df.schema.fields[0].dataType.simpleString().startswith("struct<s:array<struct<f0:bigint")

    def test_explicit_schema_with_nested_types(self, session, tmp_path):
        f = tmp_path / "typed.jsonl"
        f.write_text('{"id": "7", "meta": {"score": 1}, "extra": "ignored"}\n')

        df = session.read.json(str(f), schema="id INT, meta STRUCT<score: DOUBLE>")

        assert df.columns == ["id", "meta"]
        row = df.collect()[0]
        assert row.id == 7
        assert row.meta.score == 1.0

    def test_filename_and_sampling_options(self, session, tmp_path):
        f = tmp_path / "sampled.jsonl"
        f.write_text("".join(json.dumps({"i": n}) + "\n" for n in range(100)))

        df = session.read.option("filename", True).option("samplingRatio", 0.5).json(str(f))

        assert df.columns == ["i", "filename"]
        assert df.count() == 100
        assert df.select("filename").distinct().collect()[0].filename == str(f)

    def test_multiline_array_file(self, session, tmp_path):
        f = tmp_path / "array.json"
        f.write_text('[{"id": 1, "n": {"x": "a"}}, {"id": 2}]')

        df = session.read.option("multiLine", True).json(str(f))

        assert sorted(row.id for row in df.collect()) == [1, 2]

    def test_case_colliding_keys_raise(self, session, tmp_path):
        f = tmp_path / "case.jsonl"
        f.write_text('{"id": 1, "ID": 2}\n')

        with pytest.raises(ValueError, match="differ only in case"):
            session.read.json(str(f))

    def test_json_read_does_not_break_information_schema_queries(self, session, tmp_path):
        """Registering the typed table's schema switches sqlglot to strict
        validation; unrelated tables (temp or information_schema) must still
        self-heal via DESCRIBE introspection."""
        f = tmp_path / "strict.jsonl"
        f.write_text('{"id": 1}\n')
        session.read.json(str(f)).count()

        rows = session.sql(
            "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'sqlframe_gizmosql_read_%'"
        ).collect()
        assert rows and all("_raw" not in row.table_name for row in rows)

    def test_get_json_object_returns_bare_strings_like_spark(self, session, tmp_path):
        f = tmp_path / "gjo.jsonl"
        f.write_text('{"id": 1, "doc": {"code": "FORM-1", "n": 2, "obj": {"k": "v"}}}\n')

        df = session.read.option("maxNestedFields", 1).json(str(f))  # doc -> JSON column

        row = df.select(
            F.get_json_object(df["doc"], "$.code").alias("code"),
            F.get_json_object(df["doc"], "$.n").alias("n"),
            F.get_json_object(df["doc"], "$.obj").alias("obj"),
            F.get_json_object(df["doc"], "$.missing").alias("missing"),
        ).collect()[0]
        assert (row.code, row.n, row.obj, row.missing) == ("FORM-1", "2", '{"k":"v"}', None)
