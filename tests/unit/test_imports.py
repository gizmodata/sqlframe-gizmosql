"""Unit tests for package imports."""


def test_import_main_package():
    """Test that the main package can be imported."""
    import sqlframe_gizmosql

    assert sqlframe_gizmosql is not None


def test_import_session():
    """Test that GizmoSQLSession can be imported."""
    from sqlframe_gizmosql import GizmoSQLSession

    assert GizmoSQLSession is not None
    assert hasattr(GizmoSQLSession, "builder")


def test_import_dataframe():
    """Test that DataFrame classes can be imported."""
    from sqlframe_gizmosql import (
        GizmoSQLDataFrame,
        GizmoSQLDataFrameNaFunctions,
        GizmoSQLDataFrameStatFunctions,
    )

    assert GizmoSQLDataFrame is not None
    assert GizmoSQLDataFrameNaFunctions is not None
    assert GizmoSQLDataFrameStatFunctions is not None


def test_import_catalog():
    """Test that GizmoSQLCatalog can be imported."""
    from sqlframe_gizmosql import GizmoSQLCatalog

    assert GizmoSQLCatalog is not None


def test_import_readwriter():
    """Test that reader/writer classes can be imported."""
    from sqlframe_gizmosql import GizmoSQLDataFrameReader, GizmoSQLDataFrameWriter

    assert GizmoSQLDataFrameReader is not None
    assert GizmoSQLDataFrameWriter is not None


def test_import_group():
    """Test that GizmoSQLGroupedData can be imported."""
    from sqlframe_gizmosql import GizmoSQLGroupedData

    assert GizmoSQLGroupedData is not None


def test_import_table():
    """Test that GizmoSQLTable can be imported."""
    from sqlframe_gizmosql import GizmoSQLTable

    assert GizmoSQLTable is not None


def test_import_udf():
    """Test that GizmoSQLUDFRegistration can be imported."""
    from sqlframe_gizmosql import GizmoSQLUDFRegistration

    assert GizmoSQLUDFRegistration is not None


def test_import_column():
    """Test that Column can be imported."""
    from sqlframe_gizmosql import Column

    assert Column is not None


def test_import_types():
    """Test that Row type can be imported."""
    from sqlframe_gizmosql import Row

    assert Row is not None


def test_import_window():
    """Test that Window classes can be imported."""
    from sqlframe_gizmosql import Window, WindowSpec

    assert Window is not None
    assert WindowSpec is not None


def test_import_functions():
    """Test that functions module can be imported."""
    from sqlframe_gizmosql import functions

    assert functions is not None
    # Test a few common functions
    assert hasattr(functions, "col")
    assert hasattr(functions, "lit")
    assert hasattr(functions, "sum")
    assert hasattr(functions, "count")


def test_import_activate():
    """Test that activate function can be imported."""
    from sqlframe_gizmosql import activate

    assert activate is not None
    assert callable(activate)


def test_all_exports():
    """Test that __all__ contains expected exports."""
    import sqlframe_gizmosql

    expected_exports = [
        "activate",
        "Column",
        "GizmoSQLCatalog",
        "GizmoSQLDataFrame",
        "GizmoSQLDataFrameNaFunctions",
        "GizmoSQLDataFrameReader",
        "GizmoSQLDataFrameStatFunctions",
        "GizmoSQLDataFrameWriter",
        "GizmoSQLGroupedData",
        "GizmoSQLSession",
        "GizmoSQLTable",
        "GizmoSQLUDFRegistration",
        "Row",
        "Window",
        "WindowSpec",
    ]

    for export in expected_exports:
        assert export in sqlframe_gizmosql.__all__, f"{export} not in __all__"
        assert hasattr(sqlframe_gizmosql, export), f"{export} not accessible"
