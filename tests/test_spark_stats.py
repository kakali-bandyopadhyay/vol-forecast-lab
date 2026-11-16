"""Contract tests for the author-owned PySpark compaction job."""

from __future__ import annotations

from datetime import date, datetime

import pytest

pytestmark = pytest.mark.spark
pyspark = pytest.importorskip("pyspark", reason="pyspark is not installed")

from pyspark.sql import SparkSession, types as T  # noqa: E402


@pytest.fixture(scope="module")
def spark():
    session = (
        SparkSession.builder.master("local[1]")
        .appName("test-spark-stats")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    yield session
    session.stop()


def _clean_schema(n_chars_type=T.IntegerType()):
    """Glue clean-file columns; year/doc_type live only in partition paths."""
    return T.StructType(
        [
            T.StructField("doc_id", T.StringType(), True),
            T.StructField("event_date", T.DateType(), True),
            T.StructField("url", T.StringType(), True),
            T.StructField("sha256", T.StringType(), True),
            T.StructField("text", T.StringType(), True),
            T.StructField("n_chars", n_chars_type, True),
            T.StructField("page_count", T.IntegerType(), True),
            T.StructField("extractor_version", T.StringType(), True),
            T.StructField("extracted_at", T.TimestampType(), True),
            T.StructField("votes_against", T.ArrayType(T.StringType()), True),
            T.StructField("vote_tally", T.StringType(), True),
            T.StructField("blocks_dropped", T.IntegerType(), True),
            T.StructField("paragraph_texts", T.ArrayType(T.StringType()), True),
            T.StructField("paragraph_pages", T.ArrayType(T.IntegerType()), True),
        ]
    )


def _row(doc_id, event_date, n_chars, year, doc_type):
    text = "x" * n_chars if isinstance(n_chars, int) else "wrong-type"
    return (
        doc_id,
        event_date,
        "https://example.test/" + doc_id,
        "a" * 64,
        text,
        n_chars,
        1,
        "test",
        datetime(2026, 1, 1),
        [],
        None,
        0,
        [],
        [],
        year,
        doc_type,
    )


def _input_schema(n_chars_type=T.IntegerType()):
    return T.StructType(
        _clean_schema(n_chars_type).fields
        + [T.StructField("year", T.StringType()), T.StructField("doc_type", T.StringType())]
    )


def test_compact_writes_partitioned_documents_and_exact_group_statistics(spark, tmp_path):
    from spark.compact_stats import compact_and_summarize

    source = tmp_path / "synthetic-clean"
    output = tmp_path / "curated"
    rows = [
        _row("statement-20200101", date(2020, 1, 1), 10, "2020", "statement"),
        _row("statement-20200301", date(2020, 3, 1), 20, "2020", "statement"),
        _row("minutes-20210101", date(2021, 1, 1), 7, "2021", "minutes"),
    ]
    spark.createDataFrame(rows, _input_schema()).write.partitionBy("year", "doc_type").parquet(str(source))

    result = compact_and_summarize(source, output)

    assert result == {"n_docs": 3, "n_groups": 2}
    stats = {
        (r.year, r.doc_type): r.asDict()
        for r in spark.read.parquet(str(output / "stats.parquet")).collect()
    }
    assert set(stats) == {("2020", "statement"), ("2021", "minutes")}
    assert stats[("2020", "statement")] == {
        "year": "2020",
        "doc_type": "statement",
        "n_docs": 2,
        "mean_n_chars": 15.0,
        "median_n_chars": 15.0,
        "first_event_date": date(2020, 1, 1),
        "last_event_date": date(2020, 3, 1),
    }
    assert stats[("2021", "minutes")] == {
        "year": "2021",
        "doc_type": "minutes",
        "n_docs": 1,
        "mean_n_chars": 7.0,
        "median_n_chars": 7.0,
        "first_event_date": date(2021, 1, 1),
        "last_event_date": date(2021, 1, 1),
    }
    document_files = list((output / "documents").rglob("*.parquet"))
    assert len(document_files) == 2


def test_compact_rejects_wrong_parquet_column_type(spark, tmp_path):
    from spark.compact_stats import compact_and_summarize

    source = tmp_path / "wrong-type-clean"
    output = tmp_path / "curated"
    rows = [_row("statement-20200101", date(2020, 1, 1), "ten", "2020", "statement")]
    spark.createDataFrame(rows, _input_schema(T.StringType())).write.partitionBy("year", "doc_type").parquet(str(source))

    with pytest.raises(Exception) as excinfo:
        compact_and_summarize(source, output)
    assert not isinstance(excinfo.value, NotImplementedError)
    assert "n_chars" in str(excinfo.value) or "type" in str(excinfo.value).lower()
