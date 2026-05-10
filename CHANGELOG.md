# Changelog

All notable changes to sqlframe-gizmosql will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
