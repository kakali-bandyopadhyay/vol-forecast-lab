"""Compare pandas document statistics with the PySpark curated statistics."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd


STATS_COLUMNS = [
    "year",
    "doc_type",
    "n_docs",
    "mean_n_chars",
    "median_n_chars",
    "first_event_date",
    "last_event_date",
]
DATE_COLUMNS = ("first_event_date", "last_event_date")
FLOAT_COLUMNS = ("mean_n_chars", "median_n_chars")
KEY_COLUMNS = ("year", "doc_type")


def pandas_stats(documents: pd.DataFrame) -> pd.DataFrame:
    """Calculate reference statistics from a documents frame."""
    required = {"event_date", "doc_type", "n_chars"}
    missing = required - set(documents.columns)
    if missing:
        raise ValueError(f"documents frame is missing columns: {sorted(missing)}")
    frame = documents.loc[:, ["event_date", "doc_type", "n_chars"]].copy()
    frame["event_date"] = pd.to_datetime(frame["event_date"], errors="raise").dt.date
    frame["year"] = pd.to_datetime(frame["event_date"].astype(str)).dt.year.astype(str)
    frame["n_chars"] = pd.to_numeric(frame["n_chars"], errors="raise")
    result = (
        frame.groupby(["year", "doc_type"], as_index=False, dropna=False)
        .agg(
            n_docs=("n_chars", "size"),
            mean_n_chars=("n_chars", "mean"),
            median_n_chars=("n_chars", "median"),
            first_event_date=("event_date", "min"),
            last_event_date=("event_date", "max"),
        )
        .loc[:, STATS_COLUMNS]
    )
    result["year"] = result["year"].astype(str)
    result["n_docs"] = result["n_docs"].astype("int64")
    return result


def compare_stats(documents: pd.DataFrame, spark_stats: pd.DataFrame) -> pd.DataFrame:
    """Return one comparison row per group, with exact and tolerant checks."""
    missing = set(STATS_COLUMNS) - set(spark_stats.columns)
    if missing:
        raise ValueError(f"Spark stats are missing columns: {sorted(missing)}")
    expected = pandas_stats(documents).rename(
        columns={column: f"pandas_{column}" for column in STATS_COLUMNS if column not in KEY_COLUMNS}
    )
    actual = spark_stats.loc[:, STATS_COLUMNS].copy()
    actual["year"] = actual["year"].astype(str)
    merged = expected.merge(actual, on=list(KEY_COLUMNS), how="outer", indicator=True, validate="one_to_one")
    merged["counts_dates_pass"] = merged["_merge"].eq("both")
    for column in ("n_docs", *DATE_COLUMNS):
        left = merged[f"pandas_{column}"]
        right = merged[column]
        if column in DATE_COLUMNS:
            left = pd.to_datetime(left, errors="coerce").dt.strftime("%Y-%m-%d")
            right = pd.to_datetime(right, errors="coerce").dt.strftime("%Y-%m-%d")
        merged["counts_dates_pass"] &= left.eq(right).fillna(False)
    merged["numeric_pass"] = merged["_merge"].eq("both")
    for column in FLOAT_COLUMNS:
        difference = (merged[f"pandas_{column}"] - merged[column]).abs()
        merged[f"{column}_diff"] = difference
        merged["numeric_pass"] &= difference.le(1e-9).fillna(False)
    merged["pass"] = merged["counts_dates_pass"] & merged["numeric_pass"]
    return merged.drop(columns="_merge").sort_values(list(KEY_COLUMNS), kind="stable").reset_index(drop=True)


def _parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--documents",
        type=Path,
        default=repo_root / "data/text/documents.parquet",
        help="input document parquet (default: data/text/documents.parquet)",
    )
    parser.add_argument(
        "--spark-stats",
        type=Path,
        default=repo_root / "data/lake/curated/stats.parquet",
        help="PySpark stats parquet (default: data/lake/curated/stats.parquet)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repo_root / "results/text/10c_spark_stats.parquet",
        help="joined comparison output (default: results/text/10c_spark_stats.parquet)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.documents.is_file():
        print(f"FAIL: documents parquet not found: {args.documents}", file=sys.stderr)
        return 1
    if not args.spark_stats.exists():  # P7j: Spark writes a folder
        print(f"FAIL: Spark stats parquet not found: {args.spark_stats}", file=sys.stderr)
        return 1
    documents = pd.read_parquet(args.documents)
    spark_stats = pd.read_parquet(args.spark_stats)
    comparison = compare_stats(documents, spark_stats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    comparison.to_parquet(args.output, index=False)
    for year, doc_type, passed in comparison[["year", "doc_type", "pass"]].itertuples(index=False, name=None):
        status = "PASS" if passed else "FAIL"
        print(f"{status} year={year} doc_type={doc_type}")
    failed = int((~comparison["pass"]).sum())
    print(f"{'FAIL' if failed else 'PASS'}: {len(comparison) - failed}/{len(comparison)} groups; comparison={args.output}")
    return int(failed > 0)


if __name__ == "__main__":
    raise SystemExit(main())
