from __future__ import annotations

import contextlib
import typing as t
from functools import cached_property

import pyarrow as pa
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

# GizmoSQL's Flight SQL gRPC transport defaults to a 16 MiB max message size, and a
# single adbc_ingest() call sends `data` as one Flight message. Ingesting a
# `pyarrow.Table`/`RecordBatch` larger than the limit fails with a gRPC
# ResourceExhausted error.
#
# Neither `Table.nbytes` nor pyarrow's own IPC-serialized byte count reliably
# predicts the actual size the ADBC driver ends up sending for deeply nested
# schemas (confirmed empirically against this package's own test data: a
# slice reporting 2.2 MB under both measures was rejected by the server as a
# ~20-24 MB message). So instead of trying to predict a safe batch size
# upfront, `ingest()` just tries the real call and, on a message-size
# failure, bisects the table and retries each half — the driver's own
# accept/reject response is the only signal that's actually authoritative.
_INGEST_SIZE_ERROR_MARKERS = ("ResourceExhausted", "larger than max")


def _is_ingest_size_error(exc: Exception) -> bool:
    message = str(exc)
    return any(marker in message for marker in _INGEST_SIZE_ERROR_MARKERS)


def _ingest_table_adaptive(cur: "GizmoSQLAdbcCursor", table_name: str, table: "pa.Table", mode: str) -> str:
    """Ingest `table`, bisecting on a message-size failure. Returns the mode
    subsequent sibling calls should use ("append", once anything has landed)."""
    try:
        cur.adbc_ingest(table_name=table_name, data=table, mode=mode)
        return "append"
    except Exception as exc:
        if not _is_ingest_size_error(exc):
            raise
        if table.num_rows <= 1:
            # A single row still doesn't fit — for a schema this complex, the
            # Arrow *schema message itself* (sent once per Flight stream) can
            # already approach or exceed the limit before any row data is
            # counted, so no amount of row-based chunking can help further.
            # Raise the client's own max message size instead, e.g.:
            #   GizmoSQLSession.builder.config("gizmosql.max_msg_size", 128 * 1024 * 1024)
            raise RuntimeError(
                "A single row could not be ingested because it (or the Arrow schema "
                "itself, for very deeply nested/wide schemas) still exceeds the "
                "connection's max gRPC message size — row-based chunking can't help "
                "further. Raise the limit via the 'gizmosql.max_msg_size' session "
                "builder config (or adbc_driver_gizmosql.DatabaseOptions."
                "WITH_MAX_MSG_SIZE) when creating the connection."
            ) from exc
        # "create"/"replace" run their DDL step immediately, independent of
        # the data-streaming step that just failed on size — so the table
        # now exists (empty) as a side effect even though this call raised.
        # Every retry from here on must append, not repeat create/replace.
        mode = "append" if mode in ("create", "replace") else mode
        mid = table.num_rows // 2
        mode = _ingest_table_adaptive(cur, table_name, table.slice(0, mid), mode)
        return _ingest_table_adaptive(cur, table_name, table.slice(mid), mode)


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

    @property
    def _is_duckdb(self) -> bool:
        return True

    def ingest(
        self,
        table_name: str,
        data: t.Union["pa.Table", "pa.RecordBatch", "pa.RecordBatchReader", "pd.DataFrame"],
        mode: t.Literal["append", "create", "replace", "create_append"] = "create",
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
            The data to ingest. A ``Table``/``RecordBatch`` larger than
            GizmoSQL's gRPC max message size (16 MiB by default) is
            automatically bisected and retried until it fits — a single
            oversized message otherwise fails outright. A ``RecordBatchReader``
            is sent as-is; the caller is responsible for sizing its batches.
        mode : str, default "create"
            One of ``"create"`` (error if table exists), ``"append"`` (error if
            table does not exist), ``"create_append"`` (create if missing, then
            append), or ``"replace"`` (drop existing table, then create).

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

        if isinstance(data, pa.Table):
            _ingest_table_adaptive(self._cur, table_name, data, mode)  # type: ignore
        else:
            self._cur.adbc_ingest(table_name=table_name, data=data, mode=mode)  # type: ignore
        # Not self.table(table_name): that eagerly introspects every column's
        # type via the catalog, which chokes on DuckDB type strings sqlglot
        # can't parse (e.g. an all-null column ingests as `"NULL"[]`). A plain
        # SELECT * avoids needing each column's type up front.
        return self.sql(f"SELECT * FROM {table_name}")

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
