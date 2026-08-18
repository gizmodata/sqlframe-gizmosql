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
# single adbc_ingest() call sends `data` as one (or more, for a RecordBatchReader)
# Flight DoPut message(s). Ingesting a `pyarrow.Table`/`RecordBatch` larger than the
# limit as a single message fails with a gRPC ResourceExhausted error, so we rechunk
# it into a RecordBatchReader of batches sized to stay comfortably under that limit.
_DEFAULT_INGEST_MAX_BATCH_BYTES = 4 * 1024 * 1024


def _chunk_arrow_for_ingest(table: "pa.Table", max_batch_bytes: int) -> t.Union["pa.Table", "pa.RecordBatchReader"]:
    if table.num_rows == 0 or table.nbytes <= max_batch_bytes:
        return table
    bytes_per_row = table.nbytes / table.num_rows
    max_chunksize = max(1, int(max_batch_bytes / bytes_per_row))
    return pa.RecordBatchReader.from_batches(table.schema, table.to_batches(max_chunksize=max_chunksize))


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
        max_batch_bytes: int = _DEFAULT_INGEST_MAX_BATCH_BYTES,
    ) -> GizmoSQLTable:
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
            rechunked into batches no larger than ``max_batch_bytes`` before
            sending — a single oversized message otherwise fails against
            GizmoSQL's gRPC transport (16 MiB max message size by default). A
            ``RecordBatchReader`` is sent as-is; the caller is responsible for
            sizing its batches.
        mode : str, default "create"
            One of ``"create"`` (error if table exists), ``"append"`` (error if
            table does not exist), ``"create_append"`` (create if missing, then
            append), or ``"replace"`` (drop existing table, then create).
        max_batch_bytes : int, default 4 MiB
            Target maximum size (in bytes) of each Arrow batch sent to the
            server when ``data`` is a ``Table``/``RecordBatch``. Lower this for
            schemas with very wide or deeply nested rows (e.g. JSON with large
            nested arrays), where a handful of rows can already approach the
            server's message-size limit.

        Returns
        -------
        GizmoSQLTable
            A DataFrame/Table wrapping the now-populated ``table_name``, so the
            rest of the PySpark-style workflow continues unchanged.
        """
        with contextlib.suppress(ImportError):
            from pandas import DataFrame as pd_DataFrame

            if isinstance(data, pd_DataFrame):
                data = pa.Table.from_pandas(data)

        if isinstance(data, pa.RecordBatch):
            data = pa.Table.from_batches([data])
        if isinstance(data, pa.Table):
            data = _chunk_arrow_for_ingest(data, max_batch_bytes)

        self._cur.adbc_ingest(table_name=table_name, data=data, mode=mode)  # type: ignore
        return self.table(table_name)

    class Builder(_BaseSession.Builder):
        DEFAULT_EXECUTION_DIALECT = "duckdb"

        # GizmoSQL-specific configuration keys
        GIZMOSQL_URI_KEY = "gizmosql.uri"
        GIZMOSQL_USERNAME_KEY = "gizmosql.username"
        GIZMOSQL_PASSWORD_KEY = "gizmosql.password"
        GIZMOSQL_TLS_SKIP_VERIFY_KEY = "gizmosql.tls_skip_verify"
        GIZMOSQL_AUTH_TYPE_KEY = "gizmosql.auth_type"

        def __init__(self):
            super().__init__()
            self._gizmosql_uri: t.Optional[str] = None
            self._gizmosql_user: t.Optional[str] = None
            self._gizmosql_password: t.Optional[str] = None
            self._gizmosql_tls_skip_verify: bool = False
            self._gizmosql_auth_type: t.Optional[str] = None

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

                self._session_kwargs["conn"] = GizmoSQLConnection(**connect_kwargs)

            return GizmoSQLSession(**self._session_kwargs)

        def getOrCreate(self) -> GizmoSQLSession:
            return super().getOrCreate()  # type: ignore

    builder = Builder()
