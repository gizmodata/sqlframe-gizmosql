# CLAUDE.md - Project Guide for Claude Code

## Project Overview
sqlframe-gizmosql is a [GizmoSQL](https://gizmodata.com/gizmosql) adapter for [SQLFrame](https://github.com/eakmanrq/sqlframe), providing a PySpark-compatible DataFrame API against a GizmoSQL server (DuckDB backend with Arrow Flight SQL interface).

## Key Architecture
- **Driver**: Uses `adbc-driver-gizmosql` (not `adbc-driver-flightsql`) — cleaner `connect()` API with top-level `username`, `password`, `tls_skip_verify` kwargs
- **Connection wrapper**: `sqlframe_gizmosql/connect.py` — `GizmoSQLConnection` and `GizmoSQLAdbcCursor` handle cursor lifecycle and cleanup
- **Session**: `sqlframe_gizmosql/session.py` — `GizmoSQLSession` with Builder pattern for config
- **Activate mode**: `sqlframe_gizmosql/activate.py` — patches PySpark imports to use GizmoSQL

## Version Bumps
Version is defined in ONE place:
- `pyproject.toml` — `version = "x.y.z"`

(`__init__.py` does NOT contain a version string)

## CI/CD
- **Workflow**: `.github/workflows/ci.yml`
- **Trigger**: All pushes, PRs to main, and `workflow_dispatch`
- **Jobs**: lint → unit-tests (Python 3.10-3.14) → integration-tests (Python 3.10-3.14 with GizmoSQL Docker) → build → publish-pypi → release
- **Publish/release gated on**: `startsWith(github.ref, 'refs/tags')`
- **PyPI publishing**: Trusted publishing (`id-token: write`)
- **GitHub releases**: `softprops/action-gh-release@v2` with auto-generated release notes

## Testing
- **Unit tests**: `pytest tests/unit` — no server needed
- **Integration tests**: `pytest tests/integration` — requires GizmoSQL Docker container on port 31337
- Start server: `docker compose up -d`
- Required env vars: `GIZMOSQL_URI`, `GIZMOSQL_USERNAME`, `GIZMOSQL_PASSWORD`, `GIZMOSQL_TLS_SKIP_VERIFY`
- Default credential value: `gizmosql_user` / `gizmosql_password`

## Dependencies (main)
- `sqlframe` — PySpark-compatible DataFrame engine
- `adbc-driver-gizmosql` — GizmoSQL ADBC driver; `pyarrow` is a transitive dep (don't pin separately)

## Dev Setup
```shell
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```
