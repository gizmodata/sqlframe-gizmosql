# sqlframe-gizmosql

GizmoSQL adapter for [SQLFrame](https://github.com/eakmanrq/sqlframe) - a PySpark-like DataFrame API for GizmoSQL.

## Overview

This package provides a GizmoSQL backend for SQLFrame, allowing you to use PySpark-compatible DataFrame operations against a GizmoSQL server. GizmoSQL is a database server that uses DuckDB as its execution engine with an Arrow Flight SQL interface.

## Installation

```bash
pip install sqlframe-gizmosql
```

## Requirements

- Python >= 3.10
- GizmoSQL server running and accessible

## Quick Start

```python
from sqlframe_gizmosql import GizmoSQLSession

# Create a session connected to GizmoSQL
session = GizmoSQLSession.builder.config(
    "gizmosql.uri", "grpc://localhost:31337"
).getOrCreate()

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
    .config("gizmosql.uri", "grpc://localhost:31337") \
    .config("gizmosql.username", "user") \
    .config("gizmosql.password", "password") \
    .getOrCreate()
```

### Configuration Options

| Option | Description | Default |
|--------|-------------|---------|
| `gizmosql.uri` | GizmoSQL server URI (grpc://host:port or grpc+tls://host:port) | `grpc://localhost:31337` |
| `gizmosql.username` | Username for authentication | None |
| `gizmosql.password` | Password for authentication | None |
| `gizmosql.tls_skip_verify` | Skip TLS certificate verification (for self-signed certs) | `False` |

## Features

- Full PySpark DataFrame API compatibility via SQLFrame
- Arrow Flight SQL protocol for high-performance data transfer
- Support for reading/writing various file formats (Parquet, CSV, JSON)
- Window functions
- Aggregations and groupBy operations
- Joins
- UDF registration
- Catalog operations

## Running GizmoSQL with Docker

You can run GizmoSQL locally using Docker:

```bash
docker run -d \
    --name gizmosql \
    -p 31337:31337 \
    -e GIZMOSQL_USERNAME=gizmosql_username \
    -e GIZMOSQL_PASSWORD=gizmosql_password \
    -e TLS_ENABLED=1 \
    gizmodata/gizmosql:latest
```

For TLS connections, use `grpc+tls://` in the URI and set `gizmosql.tls_skip_verify` to `True` for self-signed certificates.

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
- [GizmoSQL](https://github.com/voltrondata/gizmosql) - Database server using DuckDB with Arrow Flight SQL interface
- [sqlmesh-gizmosql](https://github.com/gizmodata/sqlmesh-gizmosql) - GizmoSQL adapter for SQLMesh
