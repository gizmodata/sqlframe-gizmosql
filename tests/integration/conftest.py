"""Pytest fixtures for integration tests."""

import os

import pytest


@pytest.fixture(scope="session")
def gizmosql_uri():
    """Get the GizmoSQL server URI from environment or use default."""
    return os.environ.get("GIZMOSQL_URI", "grpc://localhost:31337")


@pytest.fixture(scope="session")
def gizmosql_username():
    """Get the GizmoSQL username from environment."""
    return os.environ.get("GIZMOSQL_USERNAME")


@pytest.fixture(scope="session")
def gizmosql_password():
    """Get the GizmoSQL password from environment."""
    return os.environ.get("GIZMOSQL_PASSWORD")


@pytest.fixture(scope="session")
def gizmosql_tls_skip_verify():
    """Get the TLS skip verify setting from environment."""
    return os.environ.get("GIZMOSQL_TLS_SKIP_VERIFY", "true").lower() == "true"


@pytest.fixture(scope="session")
def session(gizmosql_uri, gizmosql_username, gizmosql_password, gizmosql_tls_skip_verify):
    """Create a GizmoSQLSession for testing."""
    from sqlframe_gizmosql import GizmoSQLSession

    builder = GizmoSQLSession.builder.config("gizmosql.uri", gizmosql_uri)

    if gizmosql_username:
        builder = builder.config("gizmosql.username", gizmosql_username)
    if gizmosql_password:
        builder = builder.config("gizmosql.password", gizmosql_password)
    if gizmosql_tls_skip_verify:
        builder = builder.config("gizmosql.tls_skip_verify", True)

    session = builder.getOrCreate()
    yield session
    session.stop()
