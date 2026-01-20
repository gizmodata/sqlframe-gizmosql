"""Integration tests for GizmoSQLSession."""


class TestGizmoSQLSession:
    """Tests for GizmoSQLSession operations."""

    def test_session_creation(self, session):
        """Test that session is created successfully."""
        assert session is not None

    def test_sql_query(self, session):
        """Test executing a simple SQL query."""
        df = session.sql("SELECT 1 as value")
        result = df.collect()
        assert len(result) == 1
        assert result[0].value == 1

    def test_sql_query_with_string(self, session):
        """Test executing a SQL query with string data."""
        df = session.sql("SELECT 'hello' as message")
        result = df.collect()
        assert len(result) == 1
        assert result[0].message == "hello"

    def test_create_dataframe_from_data(self, session):
        """Test creating a DataFrame from Python data."""
        data = [(1, "Alice"), (2, "Bob"), (3, "Charlie")]
        df = session.createDataFrame(data, ["id", "name"])
        result = df.collect()
        assert len(result) == 3
        assert result[0].id == 1
        assert result[0].name == "Alice"

    def test_dataframe_show(self, session, capsys):
        """Test DataFrame show() method."""
        df = session.sql("SELECT 1 as id, 'test' as name")
        df.show()
        captured = capsys.readouterr()
        assert "id" in captured.out
        assert "name" in captured.out

    def test_dataframe_count(self, session):
        """Test DataFrame count() method."""
        data = [(1,), (2,), (3,), (4,), (5,)]
        df = session.createDataFrame(data, ["value"])
        assert df.count() == 5


class TestDataFrameOperations:
    """Tests for DataFrame operations."""

    def test_select(self, session):
        """Test select operation."""
        data = [(1, "Alice", 30), (2, "Bob", 25)]
        df = session.createDataFrame(data, ["id", "name", "age"])
        result = df.select("name", "age").collect()
        assert len(result) == 2
        assert hasattr(result[0], "name")
        assert hasattr(result[0], "age")

    def test_filter(self, session):
        """Test filter operation."""
        data = [(1, 30), (2, 25), (3, 35)]
        df = session.createDataFrame(data, ["id", "age"])
        result = df.filter("age > 28").collect()
        assert len(result) == 2

    def test_where(self, session):
        """Test where operation (alias for filter)."""
        data = [(1, 30), (2, 25), (3, 35)]
        df = session.createDataFrame(data, ["id", "age"])
        result = df.where("age < 30").collect()
        assert len(result) == 1
        assert result[0].age == 25

    def test_order_by(self, session):
        """Test orderBy operation."""
        data = [(3, "Charlie"), (1, "Alice"), (2, "Bob")]
        df = session.createDataFrame(data, ["id", "name"])
        result = df.orderBy("id").collect()
        assert result[0].id == 1
        assert result[1].id == 2
        assert result[2].id == 3

    def test_limit(self, session):
        """Test limit operation."""
        data = [(i,) for i in range(10)]
        df = session.createDataFrame(data, ["value"])
        result = df.limit(3).collect()
        assert len(result) == 3

    def test_distinct(self, session):
        """Test distinct operation."""
        data = [(1,), (2,), (1,), (3,), (2,)]
        df = session.createDataFrame(data, ["value"])
        result = df.distinct().collect()
        assert len(result) == 3

    def test_drop_duplicates(self, session):
        """Test dropDuplicates operation."""
        data = [(1, "A"), (2, "B"), (1, "A"), (3, "C")]
        df = session.createDataFrame(data, ["id", "letter"])
        result = df.dropDuplicates().collect()
        assert len(result) == 3


class TestAggregations:
    """Tests for aggregation operations."""

    def test_group_by_count(self, session):
        """Test groupBy with count."""
        data = [(1, "A"), (2, "A"), (3, "B"), (4, "A")]
        df = session.createDataFrame(data, ["id", "group"])
        result = df.groupBy("group").count().collect()
        assert len(result) == 2

    def test_group_by_sum(self, session):
        """Test groupBy with sum."""
        from sqlframe_gizmosql import functions as F

        data = [(1, "A", 10), (2, "A", 20), (3, "B", 15)]
        df = session.createDataFrame(data, ["id", "group", "value"])
        result = df.groupBy("group").agg(F.sum("value").alias("total")).collect()
        result_dict = {r.group: r.total for r in result}
        assert result_dict["A"] == 30
        assert result_dict["B"] == 15

    def test_group_by_avg(self, session):
        """Test groupBy with avg."""
        from sqlframe_gizmosql import functions as F

        data = [(1, "A", 10), (2, "A", 20), (3, "B", 15)]
        df = session.createDataFrame(data, ["id", "group", "value"])
        result = df.groupBy("group").agg(F.avg("value").alias("average")).collect()
        result_dict = {r.group: r.average for r in result}
        assert result_dict["A"] == 15.0
        assert result_dict["B"] == 15.0


class TestJoins:
    """Tests for join operations."""

    def test_inner_join(self, session):
        """Test inner join."""
        df1 = session.createDataFrame([(1, "A"), (2, "B"), (3, "C")], ["id", "letter"])
        df2 = session.createDataFrame([(1, 100), (2, 200), (4, 400)], ["id", "value"])
        result = df1.join(df2, on="id", how="inner").collect()
        assert len(result) == 2

    def test_left_join(self, session):
        """Test left join."""
        df1 = session.createDataFrame([(1, "A"), (2, "B"), (3, "C")], ["id", "letter"])
        df2 = session.createDataFrame([(1, 100), (2, 200)], ["id", "value"])
        result = df1.join(df2, on="id", how="left").collect()
        assert len(result) == 3


class TestFunctions:
    """Tests for SQL functions."""

    def test_col_function(self, session):
        """Test col function."""
        from sqlframe_gizmosql import functions as F

        data = [(1, "hello"), (2, "world")]
        df = session.createDataFrame(data, ["id", "text"])
        result = df.select(F.col("text")).collect()
        assert result[0].text == "hello"

    def test_lit_function(self, session):
        """Test lit function."""
        from sqlframe_gizmosql import functions as F

        data = [(1,), (2,)]
        df = session.createDataFrame(data, ["id"])
        result = df.select(F.lit("constant").alias("value")).collect()
        assert result[0].value == "constant"

    def test_upper_function(self, session):
        """Test upper function."""
        from sqlframe_gizmosql import functions as F

        data = [("hello",), ("world",)]
        df = session.createDataFrame(data, ["text"])
        result = df.select(F.upper(F.col("text")).alias("upper_text")).collect()
        assert result[0].upper_text == "HELLO"

    def test_lower_function(self, session):
        """Test lower function."""
        from sqlframe_gizmosql import functions as F

        data = [("HELLO",), ("WORLD",)]
        df = session.createDataFrame(data, ["text"])
        result = df.select(F.lower(F.col("text")).alias("lower_text")).collect()
        assert result[0].lower_text == "hello"

    def test_concat_function(self, session):
        """Test concat function."""
        from sqlframe_gizmosql import functions as F

        data = [("hello", "world")]
        df = session.createDataFrame(data, ["a", "b"])
        result = df.select(F.concat(F.col("a"), F.lit(" "), F.col("b")).alias("combined")).collect()
        assert result[0].combined == "hello world"


class TestWindowFunctions:
    """Tests for window functions."""

    def test_row_number(self, session):
        """Test row_number window function."""
        from sqlframe_gizmosql import Window
        from sqlframe_gizmosql import functions as F

        data = [(1, "A", 100), (2, "A", 200), (3, "B", 150)]
        df = session.createDataFrame(data, ["id", "group", "value"])
        window = Window.partitionBy("group").orderBy("value")
        result = df.withColumn("row_num", F.row_number().over(window)).collect()
        assert any(r.row_num == 1 for r in result)

    def test_rank(self, session):
        """Test rank window function."""
        from sqlframe_gizmosql import Window
        from sqlframe_gizmosql import functions as F

        data = [(1, "A", 100), (2, "A", 100), (3, "A", 200)]
        df = session.createDataFrame(data, ["id", "group", "value"])
        window = Window.partitionBy("group").orderBy("value")
        result = df.withColumn("rank", F.rank().over(window)).collect()
        assert len(result) == 3


class TestArrowConversion:
    """Tests for Arrow table conversion."""

    def test_to_arrow(self, session):
        """Test converting DataFrame to Arrow table."""
        data = [(1, "Alice"), (2, "Bob")]
        df = session.createDataFrame(data, ["id", "name"])
        arrow_table = df.toArrow()
        assert arrow_table is not None
        assert arrow_table.num_rows == 2
        assert "id" in arrow_table.column_names
        assert "name" in arrow_table.column_names


class TestActivateMode:
    """Tests for activate mode with PySpark imports."""

    def test_activate_with_pyspark_imports(
        self, gizmosql_uri, gizmosql_username, gizmosql_password, gizmosql_tls_skip_verify
    ):
        """Test using activate() with standard PySpark imports."""
        from sqlframe_gizmosql import activate

        # Activate with GizmoSQL config
        activate(
            uri=gizmosql_uri,
            username=gizmosql_username,
            password=gizmosql_password,
            tls_skip_verify=gizmosql_tls_skip_verify,
        )

        # Now use standard PySpark imports
        from pyspark.sql import SparkSession

        spark = SparkSession.builder.getOrCreate()
        df = spark.createDataFrame([(1, "Alice"), (2, "Bob")], ["id", "name"])
        result = df.collect()

        assert len(result) == 2
        assert result[0].id == 1
        assert result[0].name == "Alice"

    def test_activate_with_pyspark_functions(
        self, gizmosql_uri, gizmosql_username, gizmosql_password, gizmosql_tls_skip_verify
    ):
        """Test using PySpark functions after activate."""
        from sqlframe_gizmosql import activate

        activate(
            uri=gizmosql_uri,
            username=gizmosql_username,
            password=gizmosql_password,
            tls_skip_verify=gizmosql_tls_skip_verify,
        )

        from pyspark.sql import SparkSession
        from pyspark.sql import functions as F

        spark = SparkSession.builder.getOrCreate()
        df = spark.createDataFrame([("hello",), ("world",)], ["text"])
        result = df.select(F.upper(F.col("text")).alias("upper_text")).collect()

        assert result[0].upper_text == "HELLO"
        assert result[1].upper_text == "WORLD"

    def test_activate_with_sql_query(
        self, gizmosql_uri, gizmosql_username, gizmosql_password, gizmosql_tls_skip_verify
    ):
        """Test running SQL queries after activate."""
        from sqlframe_gizmosql import activate

        activate(
            uri=gizmosql_uri,
            username=gizmosql_username,
            password=gizmosql_password,
            tls_skip_verify=gizmosql_tls_skip_verify,
        )

        from pyspark.sql import SparkSession

        spark = SparkSession.builder.getOrCreate()
        df = spark.sql("SELECT 42 as answer, 'test' as message")
        result = df.collect()

        assert len(result) == 1
        assert result[0].answer == 42
        assert result[0].message == "test"

    def test_activate_with_aggregations(
        self, gizmosql_uri, gizmosql_username, gizmosql_password, gizmosql_tls_skip_verify
    ):
        """Test aggregations after activate."""
        from sqlframe_gizmosql import activate

        activate(
            uri=gizmosql_uri,
            username=gizmosql_username,
            password=gizmosql_password,
            tls_skip_verify=gizmosql_tls_skip_verify,
        )

        from pyspark.sql import SparkSession
        from pyspark.sql import functions as F

        spark = SparkSession.builder.getOrCreate()
        df = spark.createDataFrame([(1, "A", 10), (2, "A", 20), (3, "B", 15)], ["id", "group", "value"])
        result = df.groupBy("group").agg(F.sum("value").alias("total")).collect()
        result_dict = {r.group: r.total for r in result}

        assert result_dict["A"] == 30
        assert result_dict["B"] == 15

    def test_activate_with_existing_connection(self, session):
        """Test using activate() with an existing connection."""
        from sqlframe_gizmosql import activate

        # Activate with existing connection
        activate(conn=session._conn)

        from pyspark.sql import SparkSession

        spark = SparkSession.builder.getOrCreate()
        df = spark.createDataFrame([(1, "test")], ["id", "value"])
        result = df.collect()

        assert len(result) == 1
        assert result[0].id == 1
