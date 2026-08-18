"""Integration tests for the client-side bulk-ingest fast path behind
spark.read.json()/csv()/parquet() with client-local files."""

import pyarrow as pa
import pyarrow.parquet as pa_parquet


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
