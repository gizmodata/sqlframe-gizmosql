# CLAUDE.md - Project Guide for Claude Code

## Project Overview
sqlframe-gizmosql is a [GizmoSQL](https://gizmodata.com/gizmosql) adapter for [SQLFrame](https://github.com/eakmanrq/sqlframe), providing a PySpark-compatible DataFrame API against a GizmoSQL server (DuckDB backend with Arrow Flight SQL interface).

## Key Architecture
- **Driver**: Uses `adbc-driver-gizmosql` (not `adbc-driver-flightsql`) — cleaner `connect()` API with top-level `username`, `password`, `tls_skip_verify` kwargs; supports OAuth/SSO via `auth_type="external"`
- **Connection wrapper**: `sqlframe_gizmosql/connect.py` — `GizmoSQLConnection` and `GizmoSQLAdbcCursor` handle cursor lifecycle and cleanup. `GizmoSQLAdbcCursor` explicitly whitelists which underlying ADBC cursor methods it proxies (no `__getattr__` passthrough) — if you need a new driver method reachable through the session (like `adbc_ingest`), it has to be added here explicitly.
- **Session**: `sqlframe_gizmosql/session.py` — `GizmoSQLSession` with Builder pattern for config. `GizmoSQLSession` (via `sqlframe`'s `_BaseSession`) is a **process-wide singleton** (`_BaseSession._instance`) — whichever session/connection is created first wins for the rest of the process; a later `GizmoSQLSession.builder...getOrCreate()` or `activate(...)` call with *different* config silently returns the *same* original session unless `.stop()` was called first (which resets the singleton). This bit both a notebook demo and an early version of a test in this repo — see git history around `GizmoSQLSession.ingest()`.
- **Activate mode**: `sqlframe_gizmosql/activate.py` — patches PySpark imports to use GizmoSQL. Since `SparkSession` is literally set to `GizmoSQLSession`, `spark = SparkSession.builder.getOrCreate()` under activate mode *is* a `GizmoSQLSession` instance — any session-level API (like `.ingest()`) is available as `spark.ingest()` for free.

## Bulk Ingestion (`GizmoSQLSession.ingest()`)
For loading a `pyarrow.Table`/`RecordBatch`/`RecordBatchReader` or `pandas.DataFrame` into a
real GizmoSQL table fast (columnar Arrow Flight via ADBC's `adbc_ingest()`), instead of
`createDataFrame()`'s row-by-row SQL `VALUES` path. See the README's "Bulk Ingestion"
section for user-facing docs. Implementation notes for anyone touching this code:

- **Chunking is adaptive, not size-predicted.** Neither `pyarrow.Table.nbytes` nor pyarrow's
  own IPC-serialized byte count reliably predicts what the ADBC driver actually sends on the
  wire for deeply nested schemas (confirmed empirically: a slice reporting ~2.2 MB under both
  measures was rejected by the server as a ~20-24 MB message). `_ingest_table_adaptive()`
  just tries the real `adbc_ingest()` call and, on a message-size failure (`ResourceExhausted`
  / "larger than max" in the error text), bisects the table and retries each half — the
  driver's own accept/reject response is the only signal that's actually authoritative.
- **`"create"`/`"replace"` run their DDL step immediately**, independent of whether the
  subsequent data-streaming step succeeds — so a failed create/replace-mode call still
  leaves the table created (empty) as a side effect. Every retry after a size-failure bisect
  must switch to `"append"`, or it hits a spurious "table already exists" error.
  `_ingest_table_adaptive()` threads this through its return value.
- **A single row can still be too big.** For very wide/deeply nested schemas, the Arrow
  *schema message itself* (sent once per Flight stream, before any row data) can already
  approach GizmoSQL's default 16 MiB max gRPC message size — no amount of row-based
  chunking fixes an oversized schema. `gizmosql.max_msg_size` (session builder config) /
  `activate(..., max_msg_size=...)` raises the connection's own limit for this case; the
  adaptive bisection raises an actionable `RuntimeError` pointing at this option when it
  bottoms out at a single row that still doesn't fit.
- **`ingest()` returns via `self.sql(f"SELECT * FROM {table_name}")`, not `self.table()`.**
  `self.table()` eagerly introspects every column's type through the catalog and chokes on
  DuckDB type strings sqlglot can't parse — e.g. an all-null column ingests as `"NULL"[]`,
  which crashes `sqlglot.exp.DataType.from_str`. A plain `SELECT *` sidesteps that.
- Real-world data used to validate this (HIS/patient-forms JSON, 49 top-level fields,
  several levels of `list<struct<...>>`) also needs several GB of *server-side* DuckDB
  memory per ~100-150 rows to materialize — separate from anything in this package, but
  worth knowing if a deployment has tight memory limits.

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
- **Unit tests**: `pytest tests/unit` — no server needed. Be careful about constructing a real
  `GizmoSQLSession` here even indirectly (e.g. via a mocked `GizmoSQLConnection`) — see the
  singleton note above; it can leak into and break integration tests run in the same pytest
  process. `tests/unit/test_session.py` mocks `GizmoSQLSession` itself too, for this reason.
- **Integration tests**: `pytest tests/integration` — `tests/integration/conftest.py` spins up
  a real GizmoSQL server as a **managed subprocess** via the `gizmosql` PyPI package
  (`gizmosql.Server(username=..., password=...)`, auto-picks a free port), *not* Docker and
  *not* `docker-compose.yml` — no manual server startup or env vars needed, just
  `pytest tests/integration`. `docker-compose.yml` in the repo root is a separate,
  Docker-based way to run a GizmoSQL server for manual/local exploration (`docker compose up
  -d`), unrelated to how the test suite itself gets a server.
- Default credential value: `gizmosql_user` / `gizmosql_password`
- `docker-compose.notebook.yml` (if present) is scaffolding for running the customer repro
  notebook (`GizmoPOC.ipynb`, if present) end-to-end in a constrained Jupyter/PySpark +
  GizmoSQL container pair — separate from `docker-compose.yml` and not part of the package
  itself; these are demo/support artifacts, not committed to the repo.

## Dependencies (main)
- `sqlframe` — PySpark-compatible DataFrame engine
- `adbc-driver-gizmosql` — GizmoSQL ADBC driver; `pyarrow` is a transitive dep (don't pin separately)

## Dev Setup
```shell
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```
