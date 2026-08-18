"""Integration tests for GizmoSQLSession.ingest() (bulk Arrow/pandas loading)."""

import pyarrow as pa
import pytest


class TestIngest:
    """Tests for bulk-loading data via session.ingest()."""

    def test_ingest_create_from_arrow_table(self, session):
        """Test ingesting a pyarrow.Table with mode='create'."""
        table_name = "ingest_create_arrow"
        arrow_table = pa.table({"id": [1, 2, 3], "name": ["Alice", "Bob", "Charlie"]})

        df = session.ingest(table_name, arrow_table, mode="create")
        result = sorted(df.collect(), key=lambda row: row.id)

        assert len(result) == 3
        assert result[0].id == 1
        assert result[0].name == "Alice"

    def test_ingest_append(self, session):
        """Test that mode='append' accumulates rows across calls."""
        table_name = "ingest_append"
        session.ingest(table_name, pa.table({"id": [1, 2]}), mode="create")
        df = session.ingest(table_name, pa.table({"id": [3, 4]}), mode="append")

        assert df.count() == 4

    def test_ingest_replace(self, session):
        """Test that mode='replace' drops existing rows before loading."""
        table_name = "ingest_replace"
        session.ingest(table_name, pa.table({"id": [1, 2, 3]}), mode="create")
        df = session.ingest(table_name, pa.table({"id": [9]}), mode="replace")

        result = df.collect()
        assert len(result) == 1
        assert result[0].id == 9

    def test_ingest_from_pandas_dataframe(self, session):
        """Test ingesting a pandas.DataFrame."""
        pd = pytest.importorskip("pandas")
        table_name = "ingest_pandas"
        pandas_df = pd.DataFrame({"id": [1, 2], "name": ["Alice", "Bob"]})

        df = session.ingest(table_name, pandas_df, mode="create")
        result = sorted(df.collect(), key=lambda row: row.id)

        assert len(result) == 2
        assert result[0].name == "Alice"

    def test_ingest_create_errors_if_table_exists(self, session):
        """Test that mode='create' against an existing table raises."""
        table_name = "ingest_create_conflict"
        session.ingest(table_name, pa.table({"id": [1]}), mode="create")

        with pytest.raises(Exception):
            session.ingest(table_name, pa.table({"id": [2]}), mode="create")

    def test_ingest_bisects_table_larger_than_message_size_limit(self, session):
        """A Table too large to send as one Flight message (GizmoSQL's gRPC
        transport default is 16 MiB) must be bisected and retried, not fail
        outright with a ResourceExhausted error."""
        table_name = "ingest_chunked_large"
        n = 40
        payload = "x" * 600_000  # ~600KB/row -> ~24MB table, over the 16 MiB limit
        table = pa.table({"id": list(range(n)), "payload": [payload] * n})
        assert table.nbytes > 16 * 1024 * 1024

        df = session.ingest(table_name, table, mode="create")

        assert df.count() == n

    def test_ingest_raises_actionable_error_when_single_row_exceeds_limit(self, session):
        """A single row too large to fit in one Flight message can't be
        bisected any further — the error should point at raising
        gizmosql.max_msg_size instead of a raw driver traceback."""
        table_name = "ingest_single_row_too_large"
        table = pa.table({"payload": ["x" * (20 * 1024 * 1024)]})  # one ~20MB row

        with pytest.raises(RuntimeError, match="max_msg_size"):
            session.ingest(table_name, table, mode="create")
