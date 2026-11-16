from __future__ import annotations

from datetime import date

import pandas as pd

from scripts.check_spark_parity import compare_stats, main, pandas_stats


def _documents():
    return pd.DataFrame(
        {
            "doc_id": ["s1", "s2", "m1"],
            "event_date": [date(2020, 1, 1), date(2020, 3, 1), date(2021, 1, 1)],
            "doc_type": ["statement", "statement", "minutes"],
            "n_chars": [10, 20, 7],
        }
    )


def test_pandas_stats_uses_event_date_year_and_same_summary_columns():
    actual = pandas_stats(_documents())
    assert list(actual.columns) == [
        "year",
        "doc_type",
        "n_docs",
        "mean_n_chars",
        "median_n_chars",
        "first_event_date",
        "last_event_date",
    ]
    assert actual.to_dict("records") == [
        {
            "year": "2020",
            "doc_type": "statement",
            "n_docs": 2,
            "mean_n_chars": 15.0,
            "median_n_chars": 15.0,
            "first_event_date": date(2020, 1, 1),
            "last_event_date": date(2020, 3, 1),
        },
        {
            "year": "2021",
            "doc_type": "minutes",
            "n_docs": 1,
            "mean_n_chars": 7.0,
            "median_n_chars": 7.0,
            "first_event_date": date(2021, 1, 1),
            "last_event_date": date(2021, 1, 1),
        },
    ]


def test_parity_comparison_passes_within_float_tolerance():
    spark_stats = pandas_stats(_documents()).copy()
    spark_stats.loc[0, "mean_n_chars"] += 5e-10
    comparison = compare_stats(_documents(), spark_stats)
    assert comparison["pass"].tolist() == [True, True]
    assert comparison["counts_dates_pass"].tolist() == [True, True]


def test_parity_comparison_fails_for_mismatched_stats():
    spark_stats = pandas_stats(_documents()).copy()
    spark_stats.loc[0, "median_n_chars"] = 999
    spark_stats.loc[1, "last_event_date"] = date(2021, 2, 1)
    spark_stats.loc[1, "n_docs"] = 2
    comparison = compare_stats(_documents(), spark_stats)
    assert comparison["pass"].tolist() == [False, False]


def test_cli_writes_joined_comparison_and_returns_failure_status(tmp_path):
    documents_path = tmp_path / "documents.parquet"
    stats_path = tmp_path / "stats.parquet"
    output_path = tmp_path / "joined" / "comparison.parquet"
    _documents().to_parquet(documents_path, index=False)
    stats = pandas_stats(_documents())
    stats.loc[0, "n_docs"] = 99
    stats.to_parquet(stats_path, index=False)

    assert main(
        [
            "--documents",
            str(documents_path),
            "--spark-stats",
            str(stats_path),
            "--output",
            str(output_path),
        ]
    ) == 1
    assert output_path.is_file()
    assert pd.read_parquet(output_path)["pass"].tolist() == [False, True]
