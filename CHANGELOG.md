# Changelog

All notable changes to sqlframe-gizmosql will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `spark.read.json()`/`.csv()`/`.parquet()` (and `spark.read.load()`) now detect
  paths that exist on the *client* filesystem, parse them locally with pyarrow,
  and bulk-load them into a session-scoped temporary table over ADBC
  `adbc_ingest()` — no server-side filesystem access, no admin gating, and no
  code changes needed versus stock PySpark. Paths not visible client-side (or
  reads with `.option("serverSideRead", True)`) fall back to the previous
  server-side `read_json()`/`read_csv()`/`read_parquet()` SQL behavior.
- `.option("jsonDocument", True)` for `spark.read.json()` — ship JSON documents
  as raw strings into a single DuckDB `JSON` column with NO client-side parsing
  or schema inference. This is the only viable route for document-shaped data
  with unstable nested schemas (e.g. MongoDB exports): typed inference unions
  every field ever seen and explodes memory client- AND server-side (a 107 MB
  patient-forms file exceeded 23 GB client RSS and OOM'd a 6 GiB server at 100
  rows; `jsonDocument` loads the same file in under a second). Query with
  `F.get_json_object()` / DuckDB JSON functions, or selectively type fields
  server-side with `from_json()`.
- `GizmoSQLSession.ingest(..., temporary=True)` — ingest into a temporary table
  scoped to the session's connection.
- Instrumentation: client-side reads and `ingest()` log per-phase timings
  (parse seconds, ingest seconds, batch counts, retry time) at INFO level.

### Changed

- `ingest()` now re-batches a `Table`/`RecordBatch` and streams it as many
  small batches inside a *single* Flight stream (schema message sent once),
  instead of bisecting and retrying whole-table sends — dramatically less
  wasted transfer for data larger than the gRPC message limit. Append modes
  stage through a temporary table plus one server-side `INSERT`, so a
  mid-stream failure can never duplicate rows.
- Bumped dependency floors: `sqlframe>=4.4.0`, `adbc-driver-gizmosql>=2.0.5`
  (2.0.5 fixes fetch-after-DDL raising in the driver's dbapi; the guard in
  `connect.py` is retained as belt-and-braces for older installed drivers).

### Fixed

- Fetching after a DDL/DML statement no longer raises
  `Cannot fetchall() before execute()` (the adbc-driver-gizmosql 2.x dbapi
  routes DDL through `execute_update()`, which produces no result set) — this
  broke server-side `spark.read.*` schema inference entirely.
- `ingest()` no longer leaves the session's shared cursor stuck in bulk-ingest
  mode (which made the next DDL fail with
  `INVALID_STATE: must set IngestTargetTable before bulk ingestion`); ingestion
  now runs on a dedicated short-lived cursor.

## [1.5.0] - 2026-08-18

### Added

- `GizmoSQLSession.ingest()` — bulk-load a `pyarrow.Table`/`RecordBatch`/
  `RecordBatchReader` or `pandas.DataFrame` into a GizmoSQL table via ADBC's
  `adbc_ingest()`, streaming columnar Arrow batches over the session's
  existing connection instead of building a row-by-row SQL `INSERT`/`VALUES`
  statement. This is dramatically faster than `createDataFrame()` for large
  datasets (e.g. a JSON file parsed client-side into Arrow), and no longer
  requires opening a second, raw ADBC connection outside of
  `sqlframe_gizmosql` to reach `adbc_ingest()`. A `Table`/`RecordBatch` too
  large to send as a single Flight message (GizmoSQL's default 16 MiB gRPC
  max) is automatically bisected and retried on a `ResourceExhausted` error,
  rather than failing outright.
- `GizmoSQLAdbcCursor.adbc_ingest()` — the underlying passthrough that makes
  the above possible; previously the cursor wrapper only proxied
  `execute`/`executemany`/fetch methods.
- `gizmosql.max_msg_size` session builder config (and matching
  `activate(..., max_msg_size=...)` kwarg) — raises the connection's max gRPC
  message size. Needed for bulk-ingesting very wide/deeply nested schemas,
  where the Arrow schema message alone (sent once per Flight stream, before
  any row data) can already approach the default limit — no amount of
  row-based chunking helps in that case, so `session.ingest()` points users
  at this option when it can't bisect its way to a message that fits.

### Changed

- README: added a note that as of v1.4.0 the project runs on
  [adbc-driver-gizmosql](https://github.com/gizmodata/gizmosql-adbc) 2.0,
  powered by the new native Go GizmoSQL ADBC driver — same API, with
  immediate DDL/DML execution, `RETURNING` support, `gizmosql://` URIs,
  and OAuth/SSO provided by the shared Go driver library used across all
  language bindings.

## [1.4.0] - 2026-07-29

### Changed

- Bumped the driver floor to `adbc-driver-gizmosql>=2.0.0` — the Go-backed
  rewrite of the driver (DDL/DML routing, RETURNING support, and OAuth now
  live in the bundled shared library). API is byte-compatible with 1.x, so
  behavior is unchanged.
- CI: bumped GitHub Actions off deprecated Node 20 majors —
  `actions/checkout@v7`, `actions/setup-python@v7`, `actions/cache@v6`,
  `actions/upload-artifact@v7`, `actions/download-artifact@v8`,
  `softprops/action-gh-release@v3`.

## [1.3.0] - 2026-07-29

### Changed

- Bumped dependency floors to the latest releases: `sqlframe>=4.3.0`,
  `adbc-driver-gizmosql>=1.3.0`, and dev tools `pytest>=9.1.1`,
  `pytest-cov>=7.1.0`, `ruff>=0.16.0`, `gizmosql>=1.35.1`.
- README: examples now use the driver's new `gizmosql://` URI scheme
  (secure by default; `?transport=tcp` for plaintext), and new sections
  document ADBC connection profiles (`profile://<name>` URIs) and the
  driver's OpenTelemetry observability support.

## [1.2.2] - 2026-05-10

### Changed

- Surface the matching `## [X.Y.Z]` section from `CHANGELOG.md` as the
  GitHub Release body, mirroring the convention used in the upstream
  [GizmoSQL](https://github.com/gizmodata/gizmosql) repo. The CI release
  job now extracts release notes via `awk` and feeds them to
  `softprops/action-gh-release@v2` via `body_path`. If the matching
  section isn't found, the release is still created (with auto-generated
  notes) and a CI warning is logged.

## [1.2.1] - 2026-05-10

### Changed

- Switched the integration-test fixture from an externally-managed
  GizmoSQL container (with connection details fed via env vars) to the
  [`gizmosql`](https://pypi.org/project/gizmosql/) PyPI package's
  managed subprocess. The fixture now starts the server itself,
  auto-picks a free port, and feeds the chosen host/port/credentials
  into the existing `GizmoSQLSession` config.
- Bumped `adbc-driver-gizmosql` minimum version to `>=1.1.6`.
- Added `gizmosql` to dev extras as the new test-fixture driver.
- The CI integration-tests job no longer needs the gizmosql service
  container or the `GIZMOSQL_*` env block.

## [1.2.0] - 2026-04-11

### Changed

- Bumped `sqlframe` minimum version from `>=3.0.0` to `>=4.1.0` (#2).

## [1.1.1] - 2026-04-11

### Changed

- Bumped `adbc-driver-gizmosql` minimum version from `>=1.1.3` to
  `>=1.1.5` (#1).

## [1.1.0] - 2026-03-30

### Added

- `auth_type="external"` support for OAuth/SSO authentication flows,
  along with `oauth_port`, `oauth_timeout`, and `open_browser`
  configuration options.

## [1.0.0] - 2026-03-30

### Changed

- Migrated from a Flight-SQL-direct client to the `adbc-driver-gizmosql`
  driver.

## [0.1.2] - earlier

### Added

- Python 3.13 and 3.14 support.
