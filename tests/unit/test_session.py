"""Unit tests for GizmoSQLSession.Builder configuration."""

from unittest.mock import patch


def test_max_msg_size_config_passed_through_as_db_kwargs():
    """gizmosql.max_msg_size should translate to the driver's
    WITH_MAX_MSG_SIZE db_kwargs option when building the connection."""
    from adbc_driver_gizmosql import DatabaseOptions

    from sqlframe_gizmosql.session import GizmoSQLSession

    captured = {}

    class FakeConnection:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def cursor(self):
            raise NotImplementedError

    # Also stub GizmoSQLSession itself: the real class is a process-wide
    # singleton (`_BaseSession._instance`), and letting `builder.session`
    # construct one for real here would leak into every other test in this
    # pytest process that expects a genuinely server-connected session.
    with (
        patch("sqlframe_gizmosql.connect.GizmoSQLConnection", FakeConnection),
        patch("sqlframe_gizmosql.session.GizmoSQLSession", lambda **kwargs: None),
    ):
        builder = GizmoSQLSession.Builder()
        # activate() (run by other tests in the same process) permanently
        # swaps GizmoSQLSession.Builder for a PatchedBuilder that injects the
        # activated connection into every new builder via ACTIVATE_CONFIG —
        # drop it so this test always builds its own connection.
        builder._session_kwargs.pop("conn", None)
        builder.config("gizmosql.uri", "grpc+tcp://localhost:31337")
        builder.config("gizmosql.max_msg_size", 64 * 1024 * 1024)
        builder.session  # noqa: B018 - triggers connection construction

    assert captured["db_kwargs"] == {DatabaseOptions.WITH_MAX_MSG_SIZE.value: str(64 * 1024 * 1024)}
