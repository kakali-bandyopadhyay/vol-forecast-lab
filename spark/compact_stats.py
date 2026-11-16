"""Codex-written (P7j, 16 Nov 2026), reviewed by Kakali."""

import argparse
from pathlib import Path
from typing import Any

from pyspark.sql import DataFrame, SparkSession, functions as F, types as T


SCHEMA = T.StructType(
    [
        T.StructField("doc_id", T.StringType(), True),
        T.StructField("event_date", T.DateType(), True),
        T.StructField("url", T.StringType(), True),
        T.StructField("sha256", T.StringType(), True),
        T.StructField("text", T.StringType(), True),
        T.StructField("n_chars", T.IntegerType(), True),
        T.StructField("page_count", T.IntegerType(), True),
        T.StructField("extractor_version", T.StringType(), True),
        # Real lake files store extracted_at as parquet timestamp without time zone; if the full-data run fails on that column, change only that field to T.TimestampNTZType() (do not change it now).
        T.StructField("extracted_at", T.TimestampType(), True),
        T.StructField("votes_against", T.ArrayType(T.StringType()), True),
        T.StructField("vote_tally", T.StringType(), True),
        T.StructField("blocks_dropped", T.IntegerType(), True),
        T.StructField("paragraph_texts", T.ArrayType(T.StringType()), True),
        T.StructField("paragraph_pages", T.ArrayType(T.IntegerType()), True),
    ]
)


def summarize(df: DataFrame) -> DataFrame:
    """Return exact document statistics grouped by year and document type."""
    return (
        df.groupBy("year", "doc_type")
        .agg(
            F.count(F.lit(1)).alias("n_docs"),
            F.avg("n_chars").alias("mean_n_chars"),
            F.expr("percentile(n_chars, 0.5)").alias("median_n_chars"),
            F.min("event_date").alias("first_event_date"),
            F.max("event_date").alias("last_event_date"),
        )
        .select(
            F.col("year").cast("string").alias("year"),
            "doc_type",
            "n_docs",
            "mean_n_chars",
            "median_n_chars",
            "first_event_date",
            "last_event_date",
        )
        .orderBy("year", "doc_type")
    )


def compact_and_summarize(input_path: Path, output_path: Path) -> dict[str, Any]:
    """Compact clean document Parquet and write curated partition statistics."""
    spark = SparkSession.builder.appName("vfl-compact-stats").getOrCreate()
    spark.conf.set("spark.sql.sources.partitionColumnTypeInference.enabled", "false")
    df = (
        spark.read.schema(SCHEMA)
        .parquet(str(input_path))
        .withColumn("year", F.col("year").cast("string"))
    )
    n_docs = df.count()

    (  # Repartitioning by both partition keys produces one output file per group.
        df.repartition("year", "doc_type")
        .write.mode("overwrite")
        .partitionBy("year", "doc_type")
        .parquet(str(Path(output_path) / "documents"))
    )
    stats = summarize(df)
    stats.coalesce(1).write.mode("overwrite").parquet(
        str(Path(output_path) / "stats.parquet")
    )
    return {"n_docs": int(n_docs), "n_groups": int(stats.count())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compact clean files and summarize them.")
    parser.add_argument("--input", type=Path, default=Path("data/lake/clean"))
    parser.add_argument("--output", type=Path, default=Path("data/lake/curated"))
    args = parser.parse_args()
    print(compact_and_summarize(args.input, args.output))
