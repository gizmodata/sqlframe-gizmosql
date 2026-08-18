from __future__ import annotations

import contextlib
import logging
import time
import typing as t
import uuid
from functools import cached_property

import pyarrow as pa
import sqlglot
from sqlframe.base.session import _BaseSession

from sqlframe_gizmosql.catalog import GizmoSQLCatalog
from sqlframe_gizmosql.dataframe import GizmoSQLDataFrame
from sqlframe_gizmosql.readwriter import (
    GizmoSQLDataFrameReader,
    GizmoSQLDataFrameWriter,
)
from sqlframe_gizmosql.table import GizmoSQLTable
from sqlframe_gizmosql.udf import GizmoSQLUDFRegistration

if t.TYPE_CHECKING:
    import pandas as pd

    from sqlframe_gizmosql.connect import GizmoSQLAdbcCursor, GizmoSQLConnection

else:
    GizmoSQLConnection = t.Any
    GizmoSQLAdbcCursor = t.Any

logger = logging.getLogger(__name__)

# GizmoSQL's Flight SQL gRPC transport defaults to a 16 MiB max message size.
# The ADBC driver sends one Flight message per Arrow record batch, so a
# `pyarrow.Table` holding its data as one big batch fails with a gRPC
# ResourceExhausted error once it crosses the limit.
#
# ingest() therefore re-batches the table and streams it as many small
# batches inside a SINGLE adbc_ingest() call — the Arrow schema message is
# sent once per stream, not once per chunk (for very wide/nested schemas the
# schema alone can be several MB, so per-chunk re-sends would dominate).
#
# Neither `Table.nbytes` nor pyarrow's own IPC-serialized byte count reliably
# predicts the actual wire size for deeply nested schemas (confirmed
# empirically: a slice reporting ~2.2 MB under both measures was rejected by
# the server as a ~20-24 MB message — roughly 10x inflation). So the nominal
# per-batch target is deliberately far below the 16 MiB limit, and on a
# size rejection the whole stream is retried with 4x fewer rows per batch —
# the driver's own accept/reject response is the only authoritative signal.
_INGEST_SIZE_ERROR_MARKERS = ("ResourceExhausted", "larger than max")
_INGEST_TARGET_BATCH_BYTES = 1 * 1024 * 1024


def _is_ingest_size_error(exc: Exception) -> bool:
    message = str(exc)
    return any(marker in message for marker in _INGEST_SIZE_ERROR_MARKERS)


def _raise_actionable_size_error(exc: Exception) -> t.NoReturn:
    # A single row per batch still doesn't fit — for a schema this complex,
    # the Arrow *schema message itself* (sent once per Flight stream) can
    # already approach or exceed the limit before any row data is counted,
    # so no amount of row-based chunking can help further.
    raise RuntimeError(
        "A single row could not be ingested because it (or the Arrow schema "
        "itself, for very deeply nested/wide schemas) still exceeds the "
        "connection's max gRPC message size — row-based chunking can't help "
        "further. Raise the limit via the 'gizmosql.max_msg_size' session "
        "builder config (or adbc_driver_gizmosql.DatabaseOptions."
        "WITH_MAX_MSG_SIZE) when creating the connection."
    ) from exc


def _ingest_table_streamed(
    cur: "GizmoSQLAdbcCursor",
    table_name: str,
    table: "pa.Table",
    mode: str,
    temporary: bool = False,
) -> t.Dict[str, t.Any]:
    """Stream `table` into `table_name` as one Flight stream of small batches.

    Only "create"/"replace" modes are safe here: a stream that fails midway
    may have landed a partial prefix of rows, and retrying with "replace"
    discards it. Append semantics are handled a level up (see ingest()) by
    staging through a temporary table. Returns instrumentation stats.
    """
    if mode not in ("create", "replace"):  # pragma: no cover - internal contract
        raise ValueError(f"_ingest_table_streamed only supports create/replace, got {mode!r}")
    bytes_per_row = table.nbytes / max(table.num_rows, 1)
    rows_per_batch = max(1, min(table.num_rows, int(_INGEST_TARGET_BATCH_BYTES / max(bytes_per_row, 1))))
    stats: t.Dict[str, t.Any] = {"attempts": 0, "wasted_seconds": 0.0}
    attempt_mode = mode
    while True:
        stats["attempts"] += 1
        started = time.perf_counter()
        try:
            batches = table.to_batches(max_chunksize=rows_per_batch)
            reader = pa.RecordBatchReader.from_batches(table.schema, iter(batches))
            cur.adbc_ingest(table_name=table_name, data=reader, mode=attempt_mode, temporary=temporary)
            stats["seconds"] = time.perf_counter() - started
            stats["rows_per_batch"] = rows_per_batch
            stats["batches"] = len(batches)
            return stats
        except Exception as exc:
            if not _is_ingest_size_error(exc):
                raise
            wasted = time.perf_counter() - started
            stats["wasted_seconds"] += wasted
            if rows_per_batch <= 1:
                _raise_actionable_size_error(exc)
            rows_per_batch = max(1, rows_per_batch // 4)
            # The failed attempt's DDL ran (and a prefix of rows may have
            # landed) — the table exists now, so every retry must "replace"
            # to discard the partial data rather than duplicate it.
            attempt_mode = "replace"
            logger.info(
                "Ingest batch size exceeded the connection's max gRPC message size after "
                "%.2fs — retrying %s with %d rows/batch",
                wasted,
                table_name,
                rows_per_batch,
            )


class GizmoSQLSession(
    _BaseSession[  # type: ignore
        GizmoSQLCatalog,
        GizmoSQLDataFrameReader,
        GizmoSQLDataFrameWriter,
        GizmoSQLDataFrame,
        GizmoSQLTable,
        GizmoSQLConnection,  # type: ignore
        GizmoSQLUDFRegistration,
    ]
):
    _catalog = GizmoSQLCatalog
    _reader = GizmoSQLDataFrameReader
    _writer = GizmoSQLDataFrameWriter
    _df = GizmoSQLDataFrame
    _table = GizmoSQLTable
    _udf_registration = GizmoSQLUDFRegistration

    def __init__(self, conn: t.Optional[GizmoSQLConnection] = None):
        if not hasattr(self, "_conn"):
            super().__init__(conn)
            self._last_result = None

    @cached_property
    def _cur(self) -> GizmoSQLAdbcCursor:  # type: ignore
        return self._conn.cursor()

    @classmethod
    def _try_get_map(cls, value: t.Any) -> t.Optional[t.Dict[str, t.Any]]:
        if value and isinstance(value, dict):
            # GizmoSQL < 1.1.0 support
            if "key" in value and "value" in value:
                return dict(zip(value["key"], value["value"]))
            # GizmoSQL >= 1.1.0 support
            # If a key is not a string then it must not represent a column and therefore must be a map
            if len([k for k in value if not isinstance(k, str)]) > 0:
                return value
        return None

    def _execute(self, sql: str) -> None:
        self._last_result = self._cur.execute(sql)  # type: ignore

    def sql(self, sqlQuery, dialect=None, qualify: bool = True):  # type: ignore[override]
        """Like the base ``sql()``, but self-healing for tables sqlframe
        doesn't know yet.

        sqlframe qualifies queries against its in-session schema registry and
        rejects references to unregistered tables ("Column 'x' could not be
        resolved") — but tables created via ``spark.sql("CREATE TABLE ...")``
        or existing on the server are never auto-registered. On that failure,
        introspect each referenced table through the catalog (DESCRIBE) and
        retry once, so plain PySpark flows like CTAS-then-SELECT just work.
        """
        try:
            return super().sql(sqlQuery, dialect=dialect, qualify=qualify)
        except sqlglot.errors.OptimizeError:
            expression = (
                sqlglot.parse_one(sqlQuery, read=dialect or self.input_dialect)
                if isinstance(sqlQuery, str)
                else sqlQuery
            )
            registered_any = False
            for table in expression.find_all(sqlglot.exp.Table):
                with contextlib.suppress(Exception):
                    # No-op for already-known tables; CTE aliases and truly
                    # missing tables fail introspection and are skipped.
                    self.catalog.add_table(table.name)
                    registered_any = True
            if not registered_any:
                raise
            # sqlglot's MappingSchema caches find() results keyed by the
            # exp.Table node; the failed qualify above cached an empty result
            # under a node shape (aliased) that add_table's invalidation
            # doesn't match, so the stale miss would survive the retry.
            find_cache = getattr(self.catalog._schema, "_find_cache", None)
            if find_cache is not None:
                find_cache.clear()
            return super().sql(sqlQuery, dialect=dialect, qualify=qualify)

    @property
    def _is_duckdb(self) -> bool:
        return True

    def ingest(
        self,
        table_name: str,
        data: t.Union["pa.Table", "pa.RecordBatch", "pa.RecordBatchReader", "pd.DataFrame"],
        mode: t.Literal["append", "create", "replace", "create_append"] = "create",
        temporary: bool = False,
    ) -> GizmoSQLDataFrame:
        """Bulk-load Arrow/pandas data into a GizmoSQL table via ADBC ``adbc_ingest()``.

        This streams ``data`` to the server as columnar Arrow batches over the
        session's existing connection, instead of the row-by-row SQL ``INSERT``/
        ``VALUES`` path used by :meth:`createDataFrame`. Use this for bulk loads
        (e.g. a :class:`pyarrow.Table` parsed client-side from a large file) —
        it's dramatically faster than building a literal SQL statement for large
        datasets.

        Parameters
        ----------
        table_name : str
            The table to create/append/replace.
        data : pyarrow.Table | pyarrow.RecordBatch | pyarrow.RecordBatchReader | pandas.DataFrame
            The data to ingest. A ``Table``/``RecordBatch`` is automatically
            re-batched and streamed as many small Flight messages inside a
            single ``adbc_ingest()`` call, so it can be arbitrarily larger
            than GizmoSQL's gRPC max message size (16 MiB by default); the
            Arrow schema is only sent once per stream. A
            ``RecordBatchReader`` is sent as-is; the caller is responsible
            for sizing its batches.
        mode : str, default "create"
            One of ``"create"`` (error if table exists), ``"append"`` (error if
            table does not exist), ``"create_append"`` (create if missing, then
            append), or ``"replace"`` (drop existing table, then create).
        temporary : bool, default False
            Create ``table_name`` as a temporary table, scoped to this
            session's connection — it disappears when the session ends and is
            invisible to other connections. Used by ``spark.read.json()``/
            ``.csv()``/``.parquet()`` to materialize client-local files
            without leaving permanent tables behind on the server.

        Returns
        -------
        GizmoSQLDataFrame
            A DataFrame wrapping the now-populated ``table_name``, so the
            rest of the PySpark-style workflow continues unchanged.
        """
        with contextlib.suppress(ImportError):
            from pandas import DataFrame as pd_DataFrame

            if isinstance(data, pd_DataFrame):
                data = pa.Table.from_pandas(data)

        if isinstance(data, pa.RecordBatch):
            data = pa.Table.from_batches([data])

        started = time.perf_counter()
        # Ingest on a dedicated, short-lived cursor rather than the session's
        # shared one. Under the 1.x driver, adbc_ingest() left the cursor's
        # ADBC statement in bulk-ingest mode and the next DDL/DML on it failed
        # with "INVALID_STATE: must set IngestTargetTable before bulk
        # ingestion"; the 2.x Go driver fixes this (resetForNewQuery), but a
        # dedicated cursor stays as cheap defense-in-depth. A fresh cursor
        # shares the same connection, so session-scoped state (temporary
        # tables included) is still visible afterwards.
        with self._conn.cursor() as ingest_cur:
            if isinstance(data, pa.Table):
                if mode in ("create", "replace"):
                    stats = _ingest_table_streamed(
                        cur=ingest_cur, table_name=table_name, table=data, mode=mode, temporary=temporary
                    )
                else:
                    stats = self._ingest_append_via_stage(
                        cur=ingest_cur, table_name=table_name, table=data, mode=mode, temporary=temporary
                    )
                logger.info(
                    "Ingested %d rows into %s in %.2fs "
                    "(%d attempt(s), %d batch(es) of <=%d rows, %.2fs lost to size-limit retries)",
                    data.num_rows,
                    table_name,
                    time.perf_counter() - started,
                    stats["attempts"],
                    stats.get("batches", 1),
                    stats.get("rows_per_batch", data.num_rows),
                    stats["wasted_seconds"],
                )
            else:
                ingest_cur.adbc_ingest(table_name=table_name, data=data, mode=mode, temporary=temporary)
                logger.info(
                    "Ingested RecordBatchReader into %s in %.2fs (caller-sized batches)",
                    table_name,
                    time.perf_counter() - started,
                )
        # Not self.table(table_name): that eagerly introspects every column's
        # type via the catalog, which chokes on DuckDB type strings sqlglot
        # can't parse (e.g. an all-null column ingests as `"NULL"[]`). A plain
        # SELECT * avoids needing each column's type up front.
        return self.sql(f"SELECT * FROM {table_name}")

    def _ingest_append_via_stage(
        self,
        cur: GizmoSQLAdbcCursor,
        table_name: str,
        table: "pa.Table",
        mode: str,
        temporary: bool,
    ) -> t.Dict[str, t.Any]:
        """Append by staging through a temporary table.

        A streamed ingest that fails midway may have landed a partial prefix
        of rows. For "create"/"replace" that's recoverable by retrying with
        "replace", but appending directly to an existing table would
        duplicate the prefix on retry. So: stream into a fresh temporary
        staging table (safe to replace on retry), then move the rows across
        with a single server-side INSERT, which either applies fully or not
        at all.
        """
        stage_name = f"{table_name}_ingest_stage_{uuid.uuid4().hex[:8]}"
        stats = _ingest_table_streamed(cur=cur, table_name=stage_name, table=table, mode="create", temporary=True)
        temp_keyword = "TEMPORARY " if temporary else ""
        try:
            if mode == "create_append":
                self._cur.execute(
                    f"CREATE {temp_keyword}TABLE IF NOT EXISTS {table_name} AS SELECT * FROM {stage_name} LIMIT 0"
                )
            self._cur.execute(f"INSERT INTO {table_name} BY NAME SELECT * FROM {stage_name}")
        finally:
            self._cur.execute(f"DROP TABLE IF EXISTS {stage_name}")
        return stats

    class Builder(_BaseSession.Builder):
        DEFAULT_EXECUTION_DIALECT = "duckdb"

        # GizmoSQL-specific configuration keys
        GIZMOSQL_URI_KEY = "gizmosql.uri"
        GIZMOSQL_USERNAME_KEY = "gizmosql.username"
        GIZMOSQL_PASSWORD_KEY = "gizmosql.password"
        GIZMOSQL_TLS_SKIP_VERIFY_KEY = "gizmosql.tls_skip_verify"
        GIZMOSQL_AUTH_TYPE_KEY = "gizmosql.auth_type"
        GIZMOSQL_MAX_MSG_SIZE_KEY = "gizmosql.max_msg_size"

        def __init__(self):
            super().__init__()
            self._gizmosql_uri: t.Optional[str] = None
            self._gizmosql_user: t.Optional[str] = None
            self._gizmosql_password: t.Optional[str] = None
            self._gizmosql_tls_skip_verify: bool = False
            self._gizmosql_auth_type: t.Optional[str] = None
            self._gizmosql_max_msg_size: t.Optional[int] = None

        def _set_config(
            self,
            key: t.Optional[str] = None,
            value: t.Optional[t.Any] = None,
            *,
            map: t.Optional[t.Dict[str, t.Any]] = None,
        ) -> None:
            # Handle GizmoSQL-specific configuration
            if key == self.GIZMOSQL_URI_KEY:
                self._gizmosql_uri = value
            elif key == self.GIZMOSQL_USERNAME_KEY:
                self._gizmosql_user = value
            elif key == self.GIZMOSQL_PASSWORD_KEY:
                self._gizmosql_password = value
            elif key == self.GIZMOSQL_TLS_SKIP_VERIFY_KEY:
                self._gizmosql_tls_skip_verify = bool(value)
            elif key == self.GIZMOSQL_AUTH_TYPE_KEY:
                self._gizmosql_auth_type = value
            elif key == self.GIZMOSQL_MAX_MSG_SIZE_KEY:
                self._gizmosql_max_msg_size = int(value)
            else:
                # Let the base class handle other configuration
                super()._set_config(key, value, map=map)

        @cached_property
        def session(self) -> GizmoSQLSession:
            # Create connection if URI is provided
            if self._gizmosql_uri and "conn" not in self._session_kwargs:
                from sqlframe_gizmosql.connect import GizmoSQLConnection

                connect_kwargs: t.Dict[str, t.Any] = {
                    "uri": self._gizmosql_uri,
                }
                if self._gizmosql_auth_type:
                    connect_kwargs["auth_type"] = self._gizmosql_auth_type
                if self._gizmosql_user:
                    connect_kwargs["username"] = self._gizmosql_user
                    connect_kwargs["password"] = self._gizmosql_password or ""
                if self._gizmosql_tls_skip_verify:
                    connect_kwargs["tls_skip_verify"] = True
                if self._gizmosql_max_msg_size:
                    from adbc_driver_gizmosql import DatabaseOptions

                    connect_kwargs["db_kwargs"] = {
                        DatabaseOptions.WITH_MAX_MSG_SIZE.value: str(self._gizmosql_max_msg_size)
                    }

                self._session_kwargs["conn"] = GizmoSQLConnection(**connect_kwargs)

            return GizmoSQLSession(**self._session_kwargs)

        def getOrCreate(self) -> GizmoSQLSession:
            return super().getOrCreate()  # type: ignore

    builder = Builder()
