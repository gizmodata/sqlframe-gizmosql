# sqlframe-gizmosql

GizmoSQL adapter for [SQLFrame](https://github.com/eakmanrq/sqlframe) - a PySpark-like DataFrame API for [GizmoSQL](https://github.com/gizmodata/gizmosql).

[<img src="https://img.shields.io/badge/GitHub-gizmodata%2Fsqlframe--gizmosql-blue.svg?logo=Github">](https://github.com/gizmodata/sqlframe-gizmosql)
[<img src="https://img.shields.io/badge/GitHub-gizmodata%2Fgizmosql-blue.svg?logo=Github">](https://github.com/gizmodata/gizmosql)
[![sqlframe-gizmosql-ci](https://github.com/gizmodata/sqlframe-gizmosql/actions/workflows/ci.yml/badge.svg)](https://github.com/gizmodata/sqlframe-gizmosql/actions/workflows/ci.yml)
[![Supported Python Versions](https://img.shields.io/pypi/pyversions/sqlframe-gizmosql)](https://pypi.org/project/sqlframe-gizmosql/)
[![PyPI version](https://badge.fury.io/py/sqlframe-gizmosql.svg)](https://badge.fury.io/py/sqlframe-gizmosql)
[![PyPI Downloads](https://img.shields.io/pepy/dt/sqlframe-gizmosql.svg)](https://pypi.org/project/sqlframe-gizmosql/)

## Overview

This package provides a GizmoSQL backend for SQLFrame, allowing you to use PySpark-compatible DataFrame operations against a GizmoSQL server. GizmoSQL is a database server that uses DuckDB as its execution engine with an Arrow Flight SQL interface.

As of v1.4.0, sqlframe-gizmosql runs on [adbc-driver-gizmosql](https://github.com/gizmodata/gizmosql-adbc) 2.0, powered by the new native Go GizmoSQL ADBC driver. The API is unchanged, and features like immediate DDL/DML execution, `RETURNING` support, `gizmosql://` connection URIs, and OAuth/SSO are now provided by the shared Go driver library used across all language bindings.

## Installation

```bash
pip install sqlframe-gizmosql
```

## Requirements

- Python >= 3.10
- GizmoSQL server running and accessible

## Quick Start

First, start a GizmoSQL server (see [Running GizmoSQL with Docker](#running-gizmosql-with-docker) below), then:

```python
from sqlframe_gizmosql import GizmoSQLSession

# Create a session connected to GizmoSQL
session = GizmoSQLSession.builder \
    .config("gizmosql.uri", "gizmosql://localhost:31337") \
    .config("gizmosql.username", "gizmosql_user") \
    .config("gizmosql.password", "gizmosql_password") \
    .config("gizmosql.tls_skip_verify", True) \
    .getOrCreate()

# Create a DataFrame from a SQL query
df = session.sql("SELECT 1 as id, 'hello' as message")

# Show the results
df.show()

# Use PySpark-like DataFrame API
df2 = session.createDataFrame([
    (1, "Alice", 30),
    (2, "Bob", 25),
    (3, "Charlie", 35),
], ["id", "name", "age"])

# Filter, select, and aggregate
result = df2.filter("age > 25").select("name", "age")
result.show()

# Group by and aggregate
df2.groupBy("age").count().show()
```

## Configuration

The session can be configured using the builder pattern:

```python
session = GizmoSQLSession.builder \
    .config("gizmosql.uri", "gizmosql://localhost:31337") \
    .config("gizmosql.username", "gizmosql_user") \
    .config("gizmosql.password", "gizmosql_password") \
    .config("gizmosql.tls_skip_verify", True) \
    .getOrCreate()
```

### Using PySpark Imports (activate mode)

You can use the `activate()` function to enable standard PySpark imports while running on GizmoSQL:

```python
from sqlframe_gizmosql import activate

# Activate GizmoSQL as the backend
activate(
    uri="gizmosql://localhost:31337",
    username="gizmosql_user",
    password="gizmosql_password",
    tls_skip_verify=True  # For self-signed certificates
)

# Now use standard PySpark imports!
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = SparkSession.builder.getOrCreate()

# Create DataFrame and use PySpark-like functions
df = spark.createDataFrame([
    (1, "alice", 100),
    (2, "bob", 200),
    (3, "alice", 150),
], ["id", "name", "amount"])

# Use functions like F.upper, F.sum, F.col, etc.
result = df.select(
    F.col("id"),
    F.upper(F.col("name")).alias("name_upper"),
    F.col("amount")
)
result.show()

# Aggregations
df.groupBy("name").agg(
    F.sum("amount").alias("total"),
    F.count("*").alias("count")
).show()
```

You can also activate with an existing connection:

```python
from sqlframe_gizmosql import activate, GizmoSQLSession

# Create session first
session = GizmoSQLSession.builder \
    .config("gizmosql.uri", "gizmosql://localhost:31337") \
    .config("gizmosql.username", "gizmosql_user") \
    .config("gizmosql.password", "gizmosql_password") \
    .config("gizmosql.tls_skip_verify", True) \
    .getOrCreate()

# Activate with existing connection
activate(conn=session._conn)

# Use PySpark imports
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()
```

### Configuration Options

| Option | Description | Default |
|--------|-------------|---------|
| `gizmosql.uri` | GizmoSQL server URI — `gizmosql://host:port` (TLS by default; add `?transport=tcp` for plaintext). Legacy `grpc+tls://` / `grpc://` schemes and `profile://<name>` URIs are also accepted | `grpc://localhost:31337` |
| `gizmosql.username` | Username for authentication | None |
| `gizmosql.password` | Password for authentication | None |
| `gizmosql.tls_skip_verify` | Skip TLS certificate verification (for self-signed certs) | `False` |
| `gizmosql.auth_type` | Authentication type (e.g., `"external"` for browser-based OAuth/SSO) | None |

### Connection URIs

The preferred URI scheme is `gizmosql://host:port`, which is **secure by default** (gRPC with TLS). Append `?transport=tcp` for a plaintext connection. The legacy `grpc+tls://`, `grpc+tcp://`, and `grpc://` schemes remain fully supported.

You can also connect via an [ADBC connection profile](https://arrow.apache.org/adbc/current/format/connection_profiles.html) — a TOML file that keeps connection details (with `{{ env_var(NAME) }}` substitution for credentials) out of your code:

```python
session = GizmoSQLSession.builder \
    .config("gizmosql.uri", "profile://my-gizmosql-server") \
    .getOrCreate()
```

### OAuth/SSO Authentication

GizmoSQL supports browser-based OAuth/SSO via `auth_type="external"`. When using external auth, no username or password is needed — a browser window will open for authentication:

```python
from sqlframe_gizmosql import GizmoSQLSession

session = GizmoSQLSession.builder \
    .config("gizmosql.uri", "gizmosql://gizmosql.example.com:31337") \
    .config("gizmosql.auth_type", "external") \
    .config("gizmosql.tls_skip_verify", True) \
    .getOrCreate()
```

Or with activate mode:

```python
from sqlframe_gizmosql import activate

activate(
    uri="gizmosql://gizmosql.example.com:31337",
    auth_type="external",
    tls_skip_verify=True
)

from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()
```

## Features

- Full PySpark DataFrame API compatibility via SQLFrame
- Arrow Flight SQL protocol for high-performance data transfer
- Support for reading/writing various file formats (Parquet, CSV, JSON)
- Window functions
- Aggregations and groupBy operations
- Joins
- UDF registration
- Catalog operations

## Observability

The underlying `adbc-driver-gizmosql` driver (v1.3.0+) emits OpenTelemetry trace spans for `Database.Open`, `Prepare`, `ExecuteQuery`, and `ExecuteUpdate`. Enable them via the standard `OTEL_*` environment variables (e.g. `OTEL_TRACES_EXPORTER=otlp`), or per-connection via the driver's `adbc.telemetry.*` options. Structured driver logging is available via `ADBC_DRIVER_FLIGHTSQL_LOG_LEVEL` (`debug`/`info`/`warn`/`error`). See the [adbc-driver-gizmosql README](https://github.com/gizmodata/adbc-driver-gizmosql#observability) for details.

## Running GizmoSQL with Docker

You can run GizmoSQL locally using Docker:

```bash
docker run -d \
    --name gizmosql \
    -p 31337:31337 \
    -e GIZMOSQL_USERNAME=gizmosql_user \
    -e GIZMOSQL_PASSWORD=gizmosql_password \
    -e DATABASE_FILENAME=/tmp/test.duckdb \
    -e TLS_ENABLED=1 \
    gizmodata/gizmosql:latest
```

The `gizmosql://` URI scheme uses TLS by default; set `gizmosql.tls_skip_verify` to `True` for self-signed certificates.

## Development

### Setup

```bash
# Clone the repository
git clone https://github.com/gizmodata/sqlframe-gizmosql.git
cd sqlframe-gizmosql

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dev dependencies
pip install -e ".[dev]"
```

### Running Tests

```bash
# Run unit tests
pytest tests/unit

# Run integration tests (requires GizmoSQL server)
pytest tests/integration
```

### Code Quality

```bash
# Run linting
ruff check .

# Run formatting
ruff format .
```

## License

Apache License 2.0

## Related Projects

- [SQLFrame](https://github.com/eakmanrq/sqlframe) - PySpark-like DataFrame API for multiple SQL backends
- [GizmoSQL](https://github.com/gizmodata/gizmosql) - Database server using DuckDB with Arrow Flight SQL interface
- [sqlmesh-gizmosql](https://github.com/gizmodata/sqlmesh-gizmosql) - GizmoSQL adapter for SQLMesh
