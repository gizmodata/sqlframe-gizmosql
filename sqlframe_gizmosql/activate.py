"""
Activate module for sqlframe-gizmosql.

This module provides an activate() function that allows using standard PySpark imports
while running queries against a GizmoSQL server.
"""

from __future__ import annotations

import sys
import typing as t
from unittest.mock import MagicMock

if t.TYPE_CHECKING:
    from sqlframe_gizmosql.connect import GizmoSQLConnection

# Store activation config
ACTIVATE_CONFIG: t.Dict[str, t.Any] = {}


def activate(
    uri: t.Optional[str] = None,
    username: t.Optional[str] = None,
    password: t.Optional[str] = None,
    tls_skip_verify: bool = False,
    conn: t.Optional["GizmoSQLConnection"] = None,
) -> None:
    """
    Activate GizmoSQL as the backend for PySpark imports.

    After calling this function, you can use standard PySpark imports like:
        from pyspark.sql import SparkSession
        from pyspark.sql import functions as F

    These will be mapped to the GizmoSQL equivalents.

    Parameters
    ----------
    uri : str, optional
        GizmoSQL server URI (e.g., "grpc://localhost:31337" or "grpc+tls://localhost:31337")
    username : str, optional
        Username for authentication
    password : str, optional
        Password for authentication
    tls_skip_verify : bool, default False
        Skip TLS certificate verification (for self-signed certs)
    conn : GizmoSQLConnection, optional
        An existing GizmoSQL connection to use. If provided, uri/username/password are ignored.

    Examples
    --------
    >>> from sqlframe_gizmosql import activate
    >>> activate(
    ...     uri="grpc+tls://localhost:31337",
    ...     username="user",
    ...     password="password",
    ...     tls_skip_verify=True
    ... )
    >>> from pyspark.sql import SparkSession
    >>> spark = SparkSession.builder.getOrCreate()
    >>> df = spark.createDataFrame([(1, "hello")], ["id", "message"])
    >>> df.show()
    """
    import sqlframe_gizmosql
    from sqlframe_gizmosql import functions, types

    # Store connection config
    if conn is not None:
        ACTIVATE_CONFIG["conn"] = conn
    elif uri is not None:
        ACTIVATE_CONFIG["uri"] = uri
        if username:
            ACTIVATE_CONFIG["username"] = username
        if password:
            ACTIVATE_CONFIG["password"] = password
        ACTIVATE_CONFIG["tls_skip_verify"] = tls_skip_verify

    # Create pyspark mock module
    pyspark_mock = MagicMock()
    pyspark_mock.__file__ = "pyspark"
    pyspark_mock.__path__ = []
    sys.modules["pyspark"] = pyspark_mock

    # Set up pyspark.sql to point to our gizmosql module
    sys.modules["pyspark.sql"] = sqlframe_gizmosql
    pyspark_mock.sql = sqlframe_gizmosql

    # Map GizmoSQL classes to PySpark names
    setattr(sqlframe_gizmosql, "SparkSession", sqlframe_gizmosql.GizmoSQLSession)
    setattr(sqlframe_gizmosql, "DataFrame", sqlframe_gizmosql.GizmoSQLDataFrame)
    setattr(sqlframe_gizmosql, "DataFrameReader", sqlframe_gizmosql.GizmoSQLDataFrameReader)
    setattr(sqlframe_gizmosql, "DataFrameWriter", sqlframe_gizmosql.GizmoSQLDataFrameWriter)
    setattr(sqlframe_gizmosql, "GroupedData", sqlframe_gizmosql.GizmoSQLGroupedData)
    setattr(sqlframe_gizmosql, "Catalog", sqlframe_gizmosql.GizmoSQLCatalog)

    # Set up submodules
    sys.modules["pyspark.sql.session"] = sqlframe_gizmosql.session
    sys.modules["pyspark.sql.dataframe"] = sqlframe_gizmosql.dataframe
    sys.modules["pyspark.sql.column"] = sqlframe_gizmosql.column
    sys.modules["pyspark.sql.functions"] = functions
    sys.modules["pyspark.sql.types"] = types
    sys.modules["pyspark.sql.window"] = sqlframe_gizmosql.window
    sys.modules["pyspark.sql.catalog"] = sqlframe_gizmosql.catalog
    sys.modules["pyspark.sql.group"] = sqlframe_gizmosql.group
    sys.modules["pyspark.sql.readwriter"] = sqlframe_gizmosql.readwriter

    # Also add to pyspark.sql attributes
    pyspark_mock.sql.functions = functions
    pyspark_mock.sql.types = types

    # Patch the GizmoSQLSession.Builder to use ACTIVATE_CONFIG
    _patch_session_builder()


def _patch_session_builder() -> None:
    """Patch the session builder to use activation config."""
    from sqlframe_gizmosql.session import GizmoSQLSession

    # Create a new Builder class that applies ACTIVATE_CONFIG
    class PatchedBuilder(GizmoSQLSession.Builder):
        def __init__(self):
            super().__init__()
            # Apply activation config
            if "conn" in ACTIVATE_CONFIG:
                self._session_kwargs["conn"] = ACTIVATE_CONFIG["conn"]
            if "uri" in ACTIVATE_CONFIG:
                self._gizmosql_uri = ACTIVATE_CONFIG["uri"]
            if "username" in ACTIVATE_CONFIG:
                self._gizmosql_username = ACTIVATE_CONFIG["username"]
            if "password" in ACTIVATE_CONFIG:
                self._gizmosql_password = ACTIVATE_CONFIG["password"]
            if ACTIVATE_CONFIG.get("tls_skip_verify"):
                self._gizmosql_tls_skip_verify = True

    # Replace the builder instance
    GizmoSQLSession.builder = PatchedBuilder()
    GizmoSQLSession.Builder = PatchedBuilder
