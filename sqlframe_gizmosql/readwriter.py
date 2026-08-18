# This code is based on code from Apache Spark under the license found in the LICENSE file located in the 'sqlframe' folder.

from __future__ import annotations

import glob
import logging
import os
import time
import typing as t
import uuid

import pyarrow as pa
from sqlframe.base.readerwriter import _BaseDataFrameReader, _BaseDataFrameWriter
from sqlframe.base.util import ensure_column_mapping, to_csv
from sqlglot import exp
from sqlglot.helper import ensure_list

if t.TYPE_CHECKING:
    from sqlframe.base._typing import OptionalPrimitiveType, PathOrPaths
    from sqlframe.base.types import StructType

    from sqlframe_gizmosql.dataframe import GizmoSQLDataFrame
    from sqlframe_gizmosql.session import GizmoSQLSession  # noqa
    from sqlframe_gizmosql.table import GizmoSQLTable  # noqa

logger = logging.getLogger(__name__)

# Formats spark.read can parse on the *client* with pyarrow and bulk-ingest to
# the server, instead of asking the server's DuckDB to read the file from its
# own filesystem (which requires the path to exist server-side and is gated
# for non-admin sessions).
_LOCAL_READ_FORMATS = ("json", "csv", "parquet")

# When a directory is passed as the path (Spark-style), which files inside it
# to pick up for each format.
_LOCAL_DIR_PATTERNS = {
    "json": ("*.json", "*.jsonl", "*.ndjson"),
    "csv": ("*.csv",),
    "parquet": ("*.parquet",),
}

# Options the client-side read path actually honors; anything else the user
# set is ignored there (with a warning), since pyarrow's readers don't take
# DuckDB read_* options.
_LOCAL_READ_HANDLED_OPTIONS = {
    "header",
    "sep",
    "delimiter",
    "multiLine",
    "encoding",
    "filename",
    "inferSchema",
    "serverSideRead",
    "jsonDocument",
    "columns",
}


def _truthy(value: t.Any) -> bool:
    return str(value).lower() in ("true", "1")


def _resolve_local_files(paths: t.List[t.Any], format: str) -> t.Optional[t.List[str]]:
    """Resolve `paths` to concrete files on the client filesystem.

    Returns None (meaning: not a client-local read) unless every path element
    resolves to at least one local file. Remote schemes (s3://, http://, ...)
    are never local.
    """
    resolved: t.List[str] = []
    for path in paths:
        path = str(path)
        if "://" in path:
            return None
        if os.path.isdir(path):
            matches: t.List[str] = []
            for pattern in _LOCAL_DIR_PATTERNS.get(format, ()):
                matches.extend(glob.glob(os.path.join(path, pattern)))
        else:
            matches = [m for m in glob.glob(path) if os.path.isfile(m)]
        if not matches:
            return None
        resolved.extend(sorted(matches))
    return resolved


def _read_json_documents_to_arrow(files: t.List[str], options: t.Dict[str, t.Any]) -> pa.Table:
    """Read JSON files as raw document strings — one row per JSONL line (or per
    file with multiLine) — with NO client-side JSON parsing or schema inference.

    This is the escape hatch for document-shaped data whose fully-typed Arrow
    representation explodes (unstable nested schemas union into millions of
    mostly-null leaf columns): the documents ship as plain strings in constant
    memory and land in a single column, to be queried with DuckDB's JSON
    functions or selectively typed server-side via from_json().
    """
    encoding = options.get("encoding") or "utf-8"
    multi_line = _truthy(options.get("multiLine", False))
    add_filename = _truthy(options.get("filename", False))
    documents: t.List[str] = []
    filenames: t.List[str] = []
    for file in files:
        with open(file, encoding=encoding) as fh:
            if multi_line:
                documents.append(fh.read())
                filenames.append(file)
            else:
                for line in fh:
                    line = line.strip()
                    if line:
                        documents.append(line)
                        filenames.append(file)
    columns: t.Dict[str, pa.Array] = {"json": pa.array(documents, type=pa.large_string())}
    if add_filename:
        columns["filename"] = pa.array(filenames, type=pa.string())
    return pa.table(columns)


def _read_local_to_arrow(
    format: str,
    files: t.List[str],
    options: t.Dict[str, t.Any],
    schema_names: t.Optional[t.List[str]] = None,
) -> pa.Table:
    """Parse client-local files into a single pyarrow Table."""
    ignored = {k for k, v in options.items() if v is not None} - _LOCAL_READ_HANDLED_OPTIONS
    if ignored:
        logger.warning(
            "Reading %s client-side with pyarrow; ignoring unsupported options: %s. "
            "Pass serverSideRead=True to force a server-side read_%s() instead.",
            format,
            sorted(ignored),
            format,
        )
    add_filename = _truthy(options.get("filename", False))
    tables = []
    for file in files:
        if format == "json":
            if _truthy(options.get("multiLine", False)):
                import json

                with open(file, encoding=options.get("encoding") or "utf-8") as fh:
                    parsed = json.load(fh)
                records = parsed if isinstance(parsed, list) else [parsed]
                table = pa.Table.from_pylist(records)
            else:
                import pyarrow.json as pa_json

                table = pa_json.read_json(file)
        elif format == "csv":
            import pyarrow.csv as pa_csv

            header = _truthy(options.get("header", False))
            if header:
                read_options = pa_csv.ReadOptions()
            elif schema_names:
                read_options = pa_csv.ReadOptions(column_names=schema_names)
            else:
                read_options = pa_csv.ReadOptions(autogenerate_column_names=True)
            delimiter = options.get("sep") or options.get("delimiter") or ","
            table = pa_csv.read_csv(
                file,
                read_options=read_options,
                parse_options=pa_csv.ParseOptions(delimiter=delimiter),
            )
            if not header and not schema_names:
                # Match Spark's autogenerated column names (_c0, _c1, ...)
                table = table.rename_columns([f"_c{i}" for i in range(table.num_columns)])
        elif format == "parquet":
            import pyarrow.parquet as pa_parquet

            table = pa_parquet.read_table(file)
        else:  # pragma: no cover - guarded by _LOCAL_READ_FORMATS
            raise ValueError(f"Unsupported client-side read format: {format}")
        if add_filename:
            table = table.append_column("filename", pa.array([file] * table.num_rows, type=pa.string()))
        tables.append(table)
    if len(tables) == 1:
        return tables[0]
    return pa.concat_tables(tables, promote_options="permissive")


class GizmoSQLDataFrameReader(_BaseDataFrameReader["GizmoSQLSession", "GizmoSQLDataFrame", "GizmoSQLTable"]):
    def load(
        self,
        path: t.Optional[PathOrPaths] = None,
        format: t.Optional[str] = None,
        schema: t.Optional[t.Union[StructType, str]] = None,
        **options: OptionalPrimitiveType,
    ) -> GizmoSQLDataFrame:
        """Loads data from a data source and returns it as a :class:`DataFrame`.

        .. versionadded:: 1.4.0

        .. versionchanged:: 3.4.0
            Supports Spark Connect.

        Parameters
        ----------
        path : str or list, t.Optional
            t.Optional string or a list of string for file-system backed data sources.
        format : str, t.Optional
            t.Optional string for format of the data source. Default to 'parquet'.
        schema : :class:`pyspark.sql.types.StructType` or str, t.Optional
            t.Optional :class:`pyspark.sql.types.StructType` for the input schema
            or a DDL-formatted string (For example ``col0 INT, col1 DOUBLE``).
        **options : dict
            all other string options

        Examples
        --------
        Load a CSV file with format, schema and options specified.

        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as d:
        ...     # Write a DataFrame into a CSV file with a header
        ...     df = spark.createDataFrame([{"age": 100, "name": "Hyukjin Kwon"}])
        ...     df.write.option("header", True).mode("overwrite").format("csv").save(d)
        ...
        ...     # Read the CSV file as a DataFrame with 'nullValue' option set to 'Hyukjin Kwon',
        ...     # and 'header' option set to `True`.
        ...     df = spark.read.load(
        ...         d, schema=df.schema, format="csv", nullValue="Hyukjin Kwon", header=True)
        ...     df.printSchema()
        ...     df.show()
        root
         |-- age: long (nullable = true)
         |-- name: string (nullable = true)
        +---+----+
        |age|name|
        +---+----+
        |100|NULL|
        +---+----+
        """
        # Merge state_options with provided options, with provided options taking precedence
        merged_options = {**self.state_options, **options}

        format = format or self.state_format_to_read
        # serverSideRead is our own option, not a DuckDB read_* named
        # parameter — pop it so it never reaches the generated SQL.
        server_side_read = _truthy(merged_options.pop("serverSideRead", False))
        # jsonDocument mode: ship the raw JSON documents as strings into a
        # single JSON column, with no client-side parsing or schema inference
        # at all. This is the only viable route for document-shaped data whose
        # nested schema is unstable across rows — typed inference (client- or
        # server-side) unions every field ever seen and explodes memory.
        if _truthy(merged_options.get("jsonDocument", False)):
            if format != "json":
                raise ValueError("The 'jsonDocument' option only applies to format='json'")
            if schema:
                raise ValueError(
                    "'jsonDocument' reads produce a single raw JSON column, so a schema "
                    "cannot be applied client-side — type the fields you need server-side "
                    "instead, e.g. SELECT from_json(json, '{...structure...}') or "
                    "(json->>'$.path')::TYPE."
                )
            if server_side_read:
                raise ValueError("'jsonDocument' is a client-side read; it cannot be combined with 'serverSideRead'")
            local_files = _resolve_local_files(paths=ensure_list(path), format="json")
            if local_files is None:
                raise FileNotFoundError(
                    f"'jsonDocument' requires the path(s) to exist on the client filesystem; nothing matched {path!r}"
                )
            return self._load_json_documents(files=local_files, options=merged_options, path=path)
        # Fast path: if the file(s) exist on the *client* filesystem, parse
        # them locally with pyarrow and bulk-ingest via ADBC adbc_ingest()
        # into a session-scoped temporary table, instead of asking the
        # server's DuckDB to read a path off its own filesystem (which only
        # works when the server can see the file, and is gated for non-admin
        # sessions). Opt out with .option("serverSideRead", True).
        if path is not None and format in _LOCAL_READ_FORMATS and not server_side_read:
            local_files = _resolve_local_files(paths=ensure_list(path), format=format)
            if local_files is not None:
                return self._load_via_client_ingest(
                    files=local_files, format=format, schema=schema, options=merged_options, path=path
                )
        if schema:
            column_mapping = ensure_column_mapping(schema)
            select_column_mapping = column_mapping.copy()
            if merged_options.get("filename"):
                select_column_mapping["filename"] = "VARCHAR"
            select_columns = [x.expression for x in self._to_casted_columns(select_column_mapping)]
            if format == "csv":
                merged_options["columns"] = column_mapping  # type: ignore
        else:
            select_columns = [exp.Star()]
        if format == "delta":
            from_clause = f"delta_scan('{path}')"
        elif format:
            merged_options.pop("inferSchema", None)
            paths = ",".join([f"'{path}'" for path in ensure_list(path)])
            from_clause = f"read_{format}([{paths}], {to_csv(merged_options)})"
        else:
            from_clause = f"'{path}'"
        df = self.session.sql(exp.select(*select_columns).from_(from_clause), qualify=False)
        if select_columns == [exp.Star()]:
            # Thread serverSideRead through the schema-inference recursion so
            # the second pass takes the same (server-side) route.
            return self.load(
                path=path, format=format, schema=df.schema, serverSideRead=server_side_read, **merged_options
            )
        self.session._last_loaded_file = path  # type: ignore
        return df

    def _load_via_client_ingest(
        self,
        files: t.List[str],
        format: str,
        schema: t.Optional[t.Union[StructType, str]],
        options: t.Dict[str, t.Any],
        path: PathOrPaths,
    ) -> GizmoSQLDataFrame:
        """Parse client-local files with pyarrow and bulk-ingest them into a
        session-scoped temporary table, returning a DataFrame over it."""
        schema_names: t.Optional[t.List[str]] = None
        select_columns: t.Optional[t.List[exp.Expression]] = None
        if schema:
            column_mapping = ensure_column_mapping(schema)
            select_column_mapping = column_mapping.copy()
            if _truthy(options.get("filename", False)):
                select_column_mapping["filename"] = "VARCHAR"
            select_columns = [x.expression for x in self._to_casted_columns(select_column_mapping)]
            schema_names = list(column_mapping)
        parse_started = time.perf_counter()
        arrow_table = _read_local_to_arrow(format=format, files=files, options=options, schema_names=schema_names)
        parse_seconds = time.perf_counter() - parse_started
        table_name = f"sqlframe_gizmosql_read_{uuid.uuid4().hex[:12]}"
        logger.info(
            "Parsed %d %s file(s) client-side in %.2fs (%d rows, %.1f MB in memory); "
            "bulk-ingesting into temporary table %s",
            len(files),
            format,
            parse_seconds,
            arrow_table.num_rows,
            arrow_table.nbytes / (1024 * 1024),
            table_name,
        )
        self.session.ingest(table_name=table_name, data=arrow_table, mode="create", temporary=True)
        if select_columns:
            df = self.session.sql(exp.select(*select_columns).from_(table_name), qualify=False)
        else:
            # Plain SELECT * (not self.session.table()) — see GizmoSQLSession.ingest()
            df = self.session.sql(f"SELECT * FROM {table_name}")
        self.session._last_loaded_file = path  # type: ignore
        return df

    def _load_json_documents(
        self,
        files: t.List[str],
        options: t.Dict[str, t.Any],
        path: PathOrPaths,
    ) -> GizmoSQLDataFrame:
        """Ship raw JSON documents into a temporary table's single JSON column."""
        read_started = time.perf_counter()
        arrow_table = _read_json_documents_to_arrow(files=files, options=options)
        read_seconds = time.perf_counter() - read_started
        table_name = f"sqlframe_gizmosql_read_{uuid.uuid4().hex[:12]}"
        logger.info(
            "Read %d raw JSON document(s) from %d file(s) in %.2fs (%.1f MB, no client-side "
            "parsing); bulk-ingesting into temporary table %s",
            arrow_table.num_rows,
            len(files),
            read_seconds,
            arrow_table.nbytes / (1024 * 1024),
            table_name,
        )
        self.session.ingest(table_name=table_name, data=arrow_table, mode="create", temporary=True)
        # Upgrade the column from VARCHAR to DuckDB's JSON type so ->/->>/
        # json_* operators work without explicit casts. Validates the documents
        # as a side effect. Best-effort: DuckDB implicitly casts VARCHAR to
        # JSON anyway, so a failure here only costs ergonomics.
        try:
            self.session._cur.execute(f'ALTER TABLE {table_name} ALTER COLUMN "json" SET DATA TYPE JSON')
        except Exception as exc:
            logger.warning("Could not convert %s.json to the JSON type (left as VARCHAR): %s", table_name, exc)
        # Register the table's (known, tiny) schema with sqlframe's catalog:
        # once ANY table schema is known to the session (e.g. a temp view was
        # created), sqlglot's qualify switches to strict column validation and
        # would reject references to this table's columns otherwise.
        column_mapping = {name: "TEXT" if name == "filename" else "JSON" for name in arrow_table.column_names}
        self.session.catalog.add_table(table_name, column_mapping=column_mapping)
        # Name the columns explicitly (they're known: json [+ filename]) so the
        # DataFrame has a concrete schema without any catalog introspection —
        # a lazy SELECT * breaks e.g. createOrReplaceTempView(). Backtick
        # quoting: session.sql() parses with the Spark input dialect, where
        # double quotes would mean string literals.
        select_list = ", ".join(f"`{name}`" for name in arrow_table.column_names)
        df = self.session.sql(f"SELECT {select_list} FROM {table_name}")
        self.session._last_loaded_file = path  # type: ignore
        return df


class GizmoSQLDataFrameWriter(_BaseDataFrameWriter["GizmoSQLSession", "GizmoSQLDataFrame"]):
    def _write(self, path: str, mode: t.Optional[str], **options):  # type: ignore
        mode, skip = self._validate_mode(path, mode)
        if skip:
            return
        if mode == "append":
            raise NotImplementedError("Append mode not supported")
        options = to_csv(options, equality_char=" ")  # type: ignore
        expressions = self._df._get_expressions()
        for i, expression in enumerate(expressions):
            if i < len(expressions) - 1:
                self._df.session._collect(expressions)
            else:
                sql = self._df.session._to_sql(expression)
                self._df.session._collect(f"COPY ({sql}) TO '{path}' ({options})")
