from __future__ import annotations

import typing as t
from functools import cached_property

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
    from sqlframe_gizmosql.connect import GizmoSQLAdbcCursor, GizmoSQLConnection

else:
    GizmoSQLConnection = t.Any
    GizmoSQLAdbcCursor = t.Any


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

    class Builder(_BaseSession.Builder):
        DEFAULT_EXECUTION_DIALECT = "duckdb"

        # GizmoSQL-specific configuration keys
        GIZMOSQL_URI_KEY = "gizmosql.uri"
        GIZMOSQL_USERNAME_KEY = "gizmosql.username"
        GIZMOSQL_PASSWORD_KEY = "gizmosql.password"
        GIZMOSQL_TLS_SKIP_VERIFY_KEY = "gizmosql.tls_skip_verify"

        def __init__(self):
            super().__init__()
            self._gizmosql_uri: t.Optional[str] = None
            self._gizmosql_username: t.Optional[str] = None
            self._gizmosql_password: t.Optional[str] = None
            self._gizmosql_tls_skip_verify: bool = False

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
                self._gizmosql_username = value
            elif key == self.GIZMOSQL_PASSWORD_KEY:
                self._gizmosql_password = value
            elif key == self.GIZMOSQL_TLS_SKIP_VERIFY_KEY:
                self._gizmosql_tls_skip_verify = bool(value)
            else:
                # Let the base class handle other configuration
                super()._set_config(key, value, map=map)

        @cached_property
        def session(self) -> GizmoSQLSession:
            # Create connection if URI is provided
            if self._gizmosql_uri and "conn" not in self._session_kwargs:
                from adbc_driver_flightsql import DatabaseOptions

                from sqlframe_gizmosql.connect import GizmoSQLConnection

                db_kwargs: t.Dict[str, str] = {}
                if self._gizmosql_username:
                    db_kwargs["username"] = self._gizmosql_username
                    db_kwargs["password"] = self._gizmosql_password or ""
                if self._gizmosql_tls_skip_verify:
                    db_kwargs[DatabaseOptions.TLS_SKIP_VERIFY.value] = "true"

                connect_kwargs: t.Dict[str, t.Any] = {"uri": self._gizmosql_uri}
                if db_kwargs:
                    connect_kwargs["db_kwargs"] = db_kwargs

                self._session_kwargs["conn"] = GizmoSQLConnection(**connect_kwargs)

            return GizmoSQLSession(**self._session_kwargs)

        def getOrCreate(self) -> GizmoSQLSession:
            return super().getOrCreate()  # type: ignore

    builder = Builder()
