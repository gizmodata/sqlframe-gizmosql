"""Pytest fixtures for integration tests.

The GizmoSQL server is started as a managed subprocess via the
[`gizmosql`](https://pypi.org/project/gizmosql/) PyPI package — Docker
is not required. Auto-picks a free port so multiple test runs can
coexist without stepping on each other.
"""

import gizmosql
import pytest


@pytest.fixture(scope="session")
def gizmosql_server():
    """Start a GizmoSQL server as a managed subprocess for the session."""
    with gizmosql.Server(
        username="gizmosql_user",
        password="gizmosql_password",
    ) as srv:
        yield srv


@pytest.fixture(scope="session")
def gizmosql_uri(gizmosql_server):
    return f"grpc+tcp://{gizmosql_server.host}:{gizmosql_server.port}"


@pytest.fixture(scope="session")
def gizmosql_user(gizmosql_server):
    return gizmosql_server.username


@pytest.fixture(scope="session")
def gizmosql_password(gizmosql_server):
    return gizmosql_server.password


@pytest.fixture(scope="session")
def gizmosql_tls_skip_verify():
    # No TLS — the bare gizmosql_server binary needs cert files for TLS,
    # so the test fixture runs plaintext.
    return False


@pytest.fixture(scope="session")
def session(gizmosql_uri, gizmosql_user, gizmosql_password, gizmosql_tls_skip_verify):
    """Create a GizmoSQLSession for testing."""
    from sqlframe_gizmosql import GizmoSQLSession

    builder = GizmoSQLSession.builder.config("gizmosql.uri", gizmosql_uri)
    builder = builder.config("gizmosql.username", gizmosql_user)
    builder = builder.config("gizmosql.password", gizmosql_password)
    if gizmosql_tls_skip_verify:
        builder = builder.config("gizmosql.tls_skip_verify", True)

    session = builder.getOrCreate()
    yield session
    session.stop()
