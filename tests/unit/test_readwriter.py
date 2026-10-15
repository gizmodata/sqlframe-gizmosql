"""Unit tests for the client-local file resolution behind spark.read.*"""

import json
import tracemalloc

import pyarrow as pa

from sqlframe_gizmosql.json_schema import JsonSchemaInferrer
from sqlframe_gizmosql.readwriter import (
    _iter_json_document_batches,
    _read_json_documents_to_arrow,
    _read_local_to_arrow,
    _resolve_local_files,
)


class TestResolveLocalFiles:
    def test_existing_file_resolves(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"id": 1}\n')

        assert _resolve_local_files(paths=[str(f)], format="json") == [str(f)]

    def test_missing_file_returns_none(self, tmp_path):
        assert _resolve_local_files(paths=[str(tmp_path / "nope.jsonl")], format="json") is None

    def test_remote_scheme_returns_none(self):
        assert _resolve_local_files(paths=["s3://bucket/data.jsonl"], format="json") is None

    def test_glob_pattern_resolves_sorted(self, tmp_path):
        for name in ("b.jsonl", "a.jsonl"):
            (tmp_path / name).write_text('{"id": 1}\n')

        resolved = _resolve_local_files(paths=[str(tmp_path / "*.jsonl")], format="json")

        assert resolved == [str(tmp_path / "a.jsonl"), str(tmp_path / "b.jsonl")]

    def test_directory_resolves_format_files(self, tmp_path):
        (tmp_path / "data.ndjson").write_text('{"id": 1}\n')
        (tmp_path / "other.txt").write_text("ignored")

        assert _resolve_local_files(paths=[str(tmp_path)], format="json") == [str(tmp_path / "data.ndjson")]

    def test_any_unresolvable_path_disables_local_read(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"id": 1}\n')

        assert _resolve_local_files(paths=[str(f), str(tmp_path / "missing.jsonl")], format="json") is None


class TestReadLocalToArrow:
    def test_csv_with_header(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("id,name\n1,Alice\n")

        table = _read_local_to_arrow(format="csv", files=[str(f)], options={"header": True})

        assert table.column_names == ["id", "name"]
        assert table.num_rows == 1

    def test_csv_without_header_gets_spark_style_names(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("1,Alice\n2,Bob\n")

        table = _read_local_to_arrow(format="csv", files=[str(f)], options={})

        assert table.column_names == ["_c0", "_c1"]
        assert table.num_rows == 2

    def test_csv_custom_separator(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("id|name\n1|Alice\n")

        table = _read_local_to_arrow(format="csv", files=[str(f)], options={"header": True, "sep": "|"})

        assert table.column_names == ["id", "name"]

    def test_parquet(self, tmp_path):
        import pyarrow.parquet as pa_parquet

        f = tmp_path / "data.parquet"
        pa_parquet.write_table(pa.table({"id": [1, 2, 3]}), f)

        table = _read_local_to_arrow(format="parquet", files=[str(f)], options={})

        assert table.column("id").to_pylist() == [1, 2, 3]

    def test_multiple_files_concatenated(self, tmp_path):
        f1 = tmp_path / "a.csv"
        f1.write_text("id\n1\n")
        f2 = tmp_path / "b.csv"
        f2.write_text("id\n2\n")

        table = _read_local_to_arrow(format="csv", files=[str(f1), str(f2)], options={"header": True})

        assert sorted(table.column("id").to_pylist()) == [1, 2]

    def test_filename_option_appends_column(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("id\n1\n")

        table = _read_local_to_arrow(format="csv", files=[str(f)], options={"filename": True, "header": True})

        assert table.column("filename").to_pylist() == [str(f)]


class TestReadJsonDocumentsToArrow:
    def test_one_row_per_line_no_parsing(self, tmp_path):
        f = tmp_path / "docs.jsonl"
        f.write_text('{"id": 1, "deep": {"a": [1, 2]}}\n\n{"id": 2}\n')

        table = _read_json_documents_to_arrow(files=[str(f)], options={})

        assert table.column_names == ["json"]
        assert table.column("json").to_pylist() == ['{"id": 1, "deep": {"a": [1, 2]}}', '{"id": 2}']

    def test_documents_are_not_validated_client_side(self, tmp_path):
        f = tmp_path / "docs.jsonl"
        f.write_text("not even json\n")

        table = _read_json_documents_to_arrow(files=[str(f)], options={})

        assert table.column("json").to_pylist() == ["not even json"]

    def test_multiline_reads_whole_file_as_one_document(self, tmp_path):
        f = tmp_path / "doc.json"
        f.write_text('{\n  "id": 1\n}\n')

        table = _read_json_documents_to_arrow(files=[str(f)], options={"multiLine": True})

        assert table.num_rows == 1
        assert table.column("json").to_pylist() == ['{\n  "id": 1\n}\n']

    def test_filename_option(self, tmp_path):
        f1 = tmp_path / "a.jsonl"
        f1.write_text('{"id": 1}\n')
        f2 = tmp_path / "b.jsonl"
        f2.write_text('{"id": 2}\n{"id": 3}\n')

        table = _read_json_documents_to_arrow(files=[str(f1), str(f2)], options={"filename": True})

        assert table.column("filename").to_pylist() == [str(f1), str(f2), str(f2)]


class TestReadJsonDocumentsWithInference:
    def test_documents_are_inferred_on_the_way_through(self, tmp_path):
        f = tmp_path / "docs.jsonl"
        f.write_text('{"id": 1, "info": {"city": "NYC"}}\n{"id": 2.5, "tags": ["a"]}\n')
        inferrer = JsonSchemaInferrer()

        table = _read_json_documents_to_arrow(files=[str(f)], options={}, inferrer=inferrer)

        assert table.num_rows == 2
        assert inferrer.documents_seen == 2
        assert inferrer.structure() == {"id": "DOUBLE", "info": {"city": "VARCHAR"}, "tags": ["VARCHAR"]}

    def test_multiline_array_splits_into_records_like_spark(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text('[{"id": 1}, {"id": 2}]')
        inferrer = JsonSchemaInferrer()

        table = _read_json_documents_to_arrow(files=[str(f)], options={"multiLine": True}, inferrer=inferrer)

        assert table.column("json").to_pylist() == ['{"id": 1}', '{"id": 2}']
        assert inferrer.structure() == {"id": "BIGINT"}

    def test_multiline_object_is_one_record(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text('{\n  "id": 1\n}')

        table = _read_json_documents_to_arrow(
            files=[str(f)], options={"multiLine": True}, inferrer=JsonSchemaInferrer()
        )

        assert table.num_rows == 1


class TestIterJsonDocumentBatches:
    def test_batches_are_bounded_and_preserve_every_document_in_order(self, tmp_path):
        files = []
        for n in range(3):
            f = tmp_path / f"part_{n}.jsonl"
            f.write_text("".join(json.dumps({"file": n, "i": i}) + "\n" for i in range(10)))
            files.append(str(f))
        stats = {}

        batches = list(
            _iter_json_document_batches(files=files, options={"filename": True}, target_batch_chars=100, stats=stats)
        )

        assert len(batches) > 1
        assert all(batch.schema.names == ["json", "filename"] for batch in batches)
        # A batch is closed by the document that reaches the target, so it can
        # only overshoot by one document.
        longest_document = max(len(row) for batch in batches for row in batch.column("json").to_pylist())
        assert all(
            sum(len(row) for row in batch.column("json").to_pylist()) < 100 + longest_document for batch in batches
        )
        documents = [json.loads(row) for batch in batches for row in batch.column("json").to_pylist()]
        assert [(d["file"], d["i"]) for d in documents] == [(n, i) for n in range(3) for i in range(10)]
        assert stats["rows"] == 30
        assert stats["batches"] == len(batches)
        assert stats["max_batch_rows"] == max(batch.num_rows for batch in batches)

    def test_empty_input_yields_no_batches(self, tmp_path):
        f = tmp_path / "empty.jsonl"
        f.write_text("\n\n")
        stats = {}

        assert list(_iter_json_document_batches(files=[str(f)], options={}, stats=stats)) == []
        assert stats["rows"] == 0

    def test_inference_sees_every_document(self, tmp_path):
        f = tmp_path / "docs.jsonl"
        f.write_text('{"a": 1}\n{"b": "x"}\n{"c": [1]}\n')
        inferrer = JsonSchemaInferrer()

        for _ in _iter_json_document_batches(files=[str(f)], options={}, inferrer=inferrer, target_batch_chars=1):
            pass

        assert inferrer.documents_seen == 3
        assert inferrer.structure() == {"a": "BIGINT", "b": "VARCHAR", "c": ["BIGINT"]}

    def test_memory_stays_bounded_by_a_batch_not_the_input(self, tmp_path):
        """Regression: v1.6.0 held every document of every file in memory before
        ingesting, so a multi-GB glob exhausted the client. Consuming the batches
        one at a time must keep Python memory near one batch, not the input."""
        document = json.dumps({"id": 1, "payload": "x" * 10_000})
        files = []
        for n in range(8):
            f = tmp_path / f"big_{n}.jsonl"
            f.write_text((document + "\n") * 500)  # ~5 MB per file, ~40 MB total
            files.append(str(f))
        target_batch_chars = 1024 * 1024

        # tracemalloc sees the Python document strings; Arrow buffers live in
        # pyarrow's own allocator, so track that separately.
        arrow_baseline = pa.total_allocated_bytes()
        arrow_peak = 0
        tracemalloc.start()
        try:
            rows = 0
            for batch in _iter_json_document_batches(
                files=files, options={}, inferrer=JsonSchemaInferrer(), target_batch_chars=target_batch_chars
            ):
                rows += batch.num_rows
                arrow_peak = max(arrow_peak, pa.total_allocated_bytes() - arrow_baseline)
            _, python_peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert rows == 4000
        # v1.6.0 peaked at ~39 MB of Python strings plus ~38 MB of Arrow here.
        assert python_peak < 8 * target_batch_chars
        assert arrow_peak < 8 * target_batch_chars
