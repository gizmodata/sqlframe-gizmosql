"""Unit tests for connect module."""


def test_gizmosql_connection_class_exists():
    """Test that GizmoSQLConnection class exists."""
    from sqlframe_gizmosql.connect import GizmoSQLConnection

    assert GizmoSQLConnection is not None


def test_gizmosql_adbc_cursor_class_exists():
    """Test that GizmoSQLAdbcCursor class exists."""
    from sqlframe_gizmosql.connect import GizmoSQLAdbcCursor

    assert GizmoSQLAdbcCursor is not None


def test_connection_has_required_methods():
    """Test that GizmoSQLConnection has required interface methods."""
    from sqlframe_gizmosql.connect import GizmoSQLConnection

    # Check for required connection methods
    assert hasattr(GizmoSQLConnection, "cursor")
    assert hasattr(GizmoSQLConnection, "close")
    assert hasattr(GizmoSQLConnection, "__enter__")
    assert hasattr(GizmoSQLConnection, "__exit__")
