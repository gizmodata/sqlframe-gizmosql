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

## Reading Local Files

`spark.read.json()`, `.csv()`, `.parquet()`, and `.load()` automatically detect paths that
exist on the **client** filesystem, parse them locally (pyarrow for CSV/Parquet; a
streaming Spark-style schema inference for JSON — see below), and bulk-load them
into a session-scoped temporary table over ADBC — no server-side file access, no admin
gating, and no code changes versus stock PySpark:

```python
df = spark.read.json("local_file.jsonl")   # parsed client-side, bulk-ingested
df.groupBy("age").count().show()
```

Paths the client can't see (or reads with `.option("serverSideRead", True)`) fall back to
the previous behavior: a server-side `read_json()`/`read_csv()`/`read_parquet()` query
against the *server's* filesystem.

### JSON schema inference (Spark-compatible)

`spark.read.json()` infers the schema exactly the way Spark does — one typed column per
top-level key, nested objects as `struct` columns, arrays as `array` columns, fields
sorted by name, and a field seen with conflicting types across records (an object here, a
string there) falling back to `string` holding the raw JSON text. Inference streams the
file one record at a time in constant memory, and the typed expansion happens server-side
with DuckDB's `from_json()` — so a 100 MB, deeply nested document export loads in a couple
of seconds and looks like it does under native Spark:

```python
df = spark.read.json("patient_forms.jsonl")
df.printSchema()
# root
#  |-- _id: struct<_oid: string> (nullable = true)
#  |-- createdat: struct<_date: string> (nullable = true)
#  |-- finalized: string (nullable = true)
#  |-- form_details: json (nullable = true)        <- see below
#  |-- formID: string (nullable = true)
#  ...
df.select(df["_id"]["_oid"].alias("id"), "formID", df["createdat"]["_date"].alias("created")).show()
```

The one deliberate difference from Spark is a **nesting budget**: a nested subtree with
more than `maxNestedFields` fields (default 1000, counted recursively) is kept as a single
`JSON` column instead of being expanded into an enormous `struct`. Spark can carry a
struct with hundreds of thousands of leaves because its schema never leaves the driver;
here it would have to cross Arrow Flight as a 16 MiB-capped schema message and be
re-parsed on every DataFrame operation. Top-level columns are never collapsed; a warning
lists any that were, and they stay fully queryable:

```python
from pyspark.sql import functions as F

df.select(F.get_json_object(df["form_details"], "$.code").alias("code")).show()  # JSONPath into it
spark.read.option("maxNestedFields", 5000).json(path)                  # raise the budget
spark.read.option("maxNestedFields", None).json(path)                  # expand everything, like Spark
```

Other options honored: `schema` (a DDL string or `StructType`, applied with `from_json()`
instead of inferring — nested types included), `samplingRatio` (fraction of records to
infer from, like Spark), `multiLine` (a file holding one object or an array of objects),
`filename`, `encoding`. Column names are matched case-insensitively (DuckDB), so a file
whose top-level keys differ only in case is rejected — use `jsonDocument` for that.

### Document-shaped JSON (`jsonDocument`)

For JSON where you don't want *any* column expansion — e.g. you're going to persist the
raw documents, or the top-level keys themselves are unstable — pass
`.option("jsonDocument", True)` to skip parsing entirely: each line ships as a raw string
and lands in a single DuckDB `JSON` column, in constant memory:

```python
from pyspark.sql import functions as F

df = spark.read.option("jsonDocument", True).json("patient_forms.jsonl")  # seconds, any nesting

# Query with the standard PySpark JSON idiom (translated to DuckDB json functions):
typed = df.select(
    F.get_json_object(df["json"], "$._id").alias("form_id"),
    F.get_json_object(df["json"], "$.finalized").cast("boolean").alias("finalized"),
)
typed.groupBy("finalized").count().show()
```

For heavier use, selectively type the fields you query once, server-side, into a real
table/view — `from_json()` extracts only the fields named in its structure argument and
ignores the rest of the document (use `json_structure()` on a sample row to generate a
starting spec):

```python
session.sql("""
    CREATE TABLE patient_forms_typed AS
    SELECT from_json(json, '{"_id": {"_oid": "VARCHAR"}, "finalized": "BOOLEAN"}') AS doc,
           json AS raw
    FROM my_raw_table
""")
```

Options honored by `jsonDocument` reads: `multiLine` (whole file as one document),
`filename` (add a filename column), `encoding`.

## Bulk Ingestion

For bulk-loading in-memory data (e.g. a `pyarrow.Table` you already have), use
`session.ingest()` instead of `session.createDataFrame()` or a server-side
`read_ndjson()`/`read_json()` query. It streams a
`pyarrow.Table`/`RecordBatch`/`RecordBatchReader` or `pandas.DataFrame` to GizmoSQL as
columnar Arrow batches over ADBC's `adbc_ingest()`, reusing the session's existing
connection — no server-side file access and no admin gating required, and dramatically
faster than either of the alternatives below for bulk data:

```python
import pyarrow.json as paj

# Parse the file into Arrow client-side, then bulk-load it into a real GizmoSQL table.
arrow_table = paj.read_json("large_file.jsonl")
df = session.ingest("patient_forms", arrow_table, mode="create")

# The rest of the PySpark-style workflow is unchanged.
df.show()
session.sql("SELECT COUNT(*) FROM patient_forms").show()
```

`mode` controls how existing data is handled:

| Mode | Behavior |
|------|----------|
| `create` (default) | Create the table and insert; error if it already exists |
| `append` | Insert into an existing table; error if it does not exist |
| `create_append` | Create the table if missing, then insert |
| `replace` | Drop the table if it exists, then create and insert |

A `Table`/`RecordBatch` of any size is automatically re-batched and streamed as many small
Flight messages inside a single ingest stream (the Arrow schema is sent once), so you don't
need to size batches by hand around GizmoSQL's default 16 MiB gRPC max message size. Append
modes stage through a temporary table plus one server-side `INSERT`, so a mid-stream
failure can never duplicate rows.

For very wide or deeply nested schemas (e.g. JSON with large nested arrays), row-based
bisection can hit a wall: the Arrow *schema message itself* — sent once per Flight stream,
before any row data — can already approach the limit on its own, in which case no amount of
splitting rows helps. `session.ingest()` raises a clear error pointing you at the fix: raise
the connection's own max message size via the `gizmosql.max_msg_size` builder config (or the
matching `activate(..., max_msg_size=...)` kwarg):

```python
session = GizmoSQLSession.builder \
    .config("gizmosql.uri", "gizmosql://localhost:31337") \
    .config("gizmosql.max_msg_size", 128 * 1024 * 1024) \
    .getOrCreate()
```

Use `session.ingest()` for bulk/large data. It's not a replacement for
`session.createDataFrame()`, which remains the right tool for small, literal in-memory
data, or for `session.sql("... read_ndjson(...) ...")`, which asks the GizmoSQL server to
read a file from its own filesystem (useful when the server already has direct access to
the file, but slower and gated for non-admin/token sessions on large files).

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
| `gizmosql.max_msg_size` | Max gRPC message size in bytes — raise for bulk-ingesting very wide/deeply nested schemas (see [Bulk Ingestion](#bulk-ingestion)) | 16 MiB (driver default) |

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
- Bulk ingestion of Arrow/pandas data via `session.ingest()` (ADBC `adbc_ingest()`)
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
