"""Collect offline parity checks and Lambda invocation evidence for Step 10c."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.testing import assert_frame_equal

from volab.data import REPO_ROOT
from volab.text import chunk

DEFAULT_LAKE = REPO_ROOT / "data" / "lake" / "clean"
DEFAULT_DOCUMENTS = REPO_ROOT / "data" / "text" / "documents.parquet"
DEFAULT_CHUNKS = REPO_ROOT / "data" / "text" / "chunks.parquet"
DEFAULT_ATHENA = REPO_ROOT / "results" / "text" / "10c_athena.json"
DEFAULT_OUTPUT = REPO_ROOT / "results" / "text" / "10c_evidence.json"
CHUNK_COLUMNS = ["chunk_id", "text", "page", "n_tokens", "content_hash"]
INDEXER_CHUNK_COLUMNS = [
    "chunk_id", "doc_id", "doc_type", "event_date", "ord",
    "page", "text", "n_tokens", "content_hash",
]


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("CloudWatch --start and --end must include a timezone")
    return parsed


def _text(row: pd.Series) -> str:
    value = row.get("text")
    if value is not None and not (isinstance(value, float) and pd.isna(value)):
        return str(value)
    paragraphs = _paragraphs(row)
    return "\n\n".join(p["text"] for p in paragraphs)


def _paragraphs(row: pd.Series) -> list[dict[str, Any]]:
    """Convert the clean row fields exactly as consumers.indexer does before chunking."""
    texts = row.get("paragraph_texts")
    pages = row.get("paragraph_pages")
    texts = [] if texts is None or (isinstance(texts, float) and pd.isna(texts)) else list(texts)
    pages = [] if pages is None or (isinstance(pages, float) and pd.isna(pages)) else list(pages)
    if len(texts) != len(pages):
        raise ValueError("paragraph_texts and paragraph_pages must have equal length")
    return [
        {"text": str(text), "page": None if page is None or pd.isna(page) else int(page)}
        for text, page in zip(texts, pages)
    ]


def _lake_rows(lake_root: Path) -> pd.DataFrame:
    paths = sorted(lake_root.rglob("*.parquet"))
    if not paths:
        raise FileNotFoundError(f"no parquet files under {lake_root}")
    return pd.concat((pd.read_parquet(path) for path in paths), ignore_index=True)


def _metric_sum(client: Any, metric: str, function: str, start: datetime, end: datetime) -> int:
    response = client.get_metric_statistics(
        Namespace="AWS/Lambda",
        MetricName=metric,
        Dimensions=[{"Name": "FunctionName", "Value": function}],
        StartTime=start,
        EndTime=end,
        Period=60,
        Statistics=["Sum"],
    )
    return int(round(sum(float(point.get("Sum", 0)) for point in response.get("Datapoints", []))))


def _chunks_from_lake(lake: pd.DataFrame) -> pd.DataFrame:
    rebuilt: list[dict[str, Any]] = []
    for _, row in lake.iterrows():
        paragraphs = _paragraphs(row)
        doc = {
            "doc_id": str(row["doc_id"]),
            "doc_type": str(row["doc_type"]),
            "event_date": pd.to_datetime(row["event_date"]).date(),
            "paragraphs": paragraphs,
        }
        # This is the same chunking call and clean-row conversion used by consumers.indexer.
        rebuilt.extend(chunk.chunk_document(doc))
    return pd.DataFrame(rebuilt, columns=INDEXER_CHUNK_COLUMNS)[CHUNK_COLUMNS]


def collect(
    *,
    cloudwatch_client: Any,
    function: str,
    start: datetime,
    end: datetime,
    lake_root: Path = DEFAULT_LAKE,
    documents_path: Path = DEFAULT_DOCUMENTS,
    chunks_path: Path = DEFAULT_CHUNKS,
    athena_path: Path = DEFAULT_ATHENA,
) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}

    try:
        invocations = _metric_sum(cloudwatch_client, "Invocations", function, start, end)
        errors = _metric_sum(cloudwatch_client, "Errors", function, start, end)
        evidence["lambda_metrics"] = {
            "status": "PASS", "function": function, "start": start.isoformat(), "end": end.isoformat(),
            "invocations": invocations, "errors": errors,
        }
    except Exception as exc:
        evidence["lambda_metrics"] = {"status": "FAIL", "error": str(exc)}

    try:
        lake = _lake_rows(lake_root)
        docs = pd.read_parquet(documents_path)
        lake_by_id = lake.assign(_text=lake.apply(_text, axis=1)).set_index("doc_id")["_text"]
        docs_by_id = docs.set_index("doc_id")["text"]
        missing = sorted(set(lake_by_id.index) - set(docs_by_id.index))
        extra = sorted(set(docs_by_id.index) - set(lake_by_id.index))
        mismatched = sorted(
            str(doc_id) for doc_id in (set(lake_by_id.index) & set(docs_by_id.index))
            if lake_by_id.loc[doc_id] != docs_by_id.loc[doc_id]
        )
        ok = not (missing or extra or mismatched)
        evidence["lake_text_parity"] = {
            "status": "PASS" if ok else "FAIL", "lake_docs": int(len(lake_by_id)),
            "document_docs": int(len(docs_by_id)), "same_doc_ids": ok and not mismatched,
            "missing_doc_ids": missing, "extra_doc_ids": extra, "text_mismatch_doc_ids": mismatched,
        }
    except Exception as exc:
        evidence["lake_text_parity"] = {"status": "FAIL", "error": str(exc)}

    try:
        lake = _lake_rows(lake_root)
        expected = _chunks_from_lake(lake).sort_values("chunk_id").reset_index(drop=True)
        actual = pd.read_parquet(chunks_path)[CHUNK_COLUMNS].sort_values("chunk_id").reset_index(drop=True)
        expected_ids, actual_ids = set(expected.chunk_id), set(actual.chunk_id)
        missing = sorted(expected_ids - actual_ids)
        extra = sorted(actual_ids - expected_ids)
        details = None
        try:
            assert_frame_equal(expected, actual, check_dtype=False, check_like=False)
        except AssertionError as exc:
            details = str(exc).splitlines()[0]
        ok = not missing and not extra and details is None
        evidence["d1_chunk_parity"] = {
            "status": "PASS" if ok else "FAIL", "rebuilt_chunks": len(expected),
            "stored_chunks": len(actual), "missing_chunk_ids": missing,
            "extra_chunk_ids": extra, "frame_mismatch": details,
        }
    except Exception as exc:
        evidence["d1_chunk_parity"] = {"status": "FAIL", "error": str(exc)}

    if athena_path.is_file():
        try:
            athena = json.loads(athena_path.read_text(encoding="utf-8"))
            evidence["athena_counts"] = {
                "status": "PASS", "present": True,
                "queries": {name: {"state": record.get("state"), "row_count": record.get("row_count", 0)}
                            for name, record in athena.items()},
            }
        except Exception as exc:
            evidence["athena_counts"] = {"status": "FAIL", "present": True, "error": str(exc)}
    else:
        evidence["athena_counts"] = {"status": "PASS", "present": False, "queries": {}}
    return evidence


def main(argv: list[str] | None = None, *, cloudwatch_client: Any | None = None,
         lake_root: Path = DEFAULT_LAKE, documents_path: Path = DEFAULT_DOCUMENTS,
         chunks_path: Path = DEFAULT_CHUNKS, athena_path: Path = DEFAULT_ATHENA,
         output_path: Path = DEFAULT_OUTPUT, report=print) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="vfl-build")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--function", default="vfl-extract")
    parser.add_argument("--start", required=True, help="ISO timestamp with timezone")
    parser.add_argument("--end", required=True, help="ISO timestamp with timezone")
    args = parser.parse_args(argv)
    start, end = _timestamp(args.start), _timestamp(args.end)
    if end <= start:
        parser.error("--end must be after --start")
    if cloudwatch_client is None:
        import boto3
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        cloudwatch_client = session.client("cloudwatch")
    evidence = collect(
        cloudwatch_client=cloudwatch_client, function=args.function, start=start, end=end,
        lake_root=lake_root, documents_path=documents_path, chunks_path=chunks_path, athena_path=athena_path,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(evidence, handle, indent=2, sort_keys=True)
        handle.write("\n")
    failed = False
    for name, check in evidence.items():
        nums = {key: value for key, value in check.items() if isinstance(value, (int, float, bool))}
        report(f"{name}: {check['status']} {json.dumps(nums, sort_keys=True)}")
        failed |= check["status"] == "FAIL"
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
