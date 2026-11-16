"""Offline tests for the Step 10c evidence collector."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from scripts import collect_10c_evidence as evidence
from volab.text import chunk

CHUNK_COLUMNS = [
    "chunk_id", "doc_id", "doc_type", "event_date", "ord",
    "page", "text", "n_tokens", "content_hash",
]


class FakeCloudWatch:
    def __init__(self):
        self.calls = []

    def get_metric_statistics(self, **kwargs):
        self.calls.append(kwargs)
        total = 7 if kwargs["MetricName"] == "Invocations" else 2
        return {"Datapoints": [{"Sum": total}]}


def _fixtures(tmp_path, *, text_mismatch=False, missing_chunk=False):
    clean = tmp_path / "lake" / "clean" / "year=2020" / "statement.parquet"
    clean.parent.mkdir(parents=True)
    lake = pd.DataFrame([
        {"doc_id": "statement-20200129", "doc_type": "statement", "event_date": "2020-01-29",
         "text": "opening paragraph\n\nsecond paragraph", "paragraph_texts": ["opening paragraph", "second paragraph"],
         "paragraph_pages": [1, 2]},
        {"doc_id": "minutes-20200610", "doc_type": "minutes", "event_date": "2020-06-10",
         "text": "meeting summary", "paragraph_texts": ["meeting summary"], "paragraph_pages": [1]},
    ])
    lake.to_parquet(clean, index=False)
    documents = tmp_path / "documents.parquet"
    doc_rows = lake[["doc_id", "text"]].copy()
    if text_mismatch:
        doc_rows.loc[0, "text"] = "different text"
    doc_rows.to_parquet(documents, index=False)
    generated = []
    for row in lake.itertuples(index=False):
        doc = {"doc_id": row.doc_id, "doc_type": row.doc_type,
               "event_date": pd.Timestamp(row.event_date).date(),
               "paragraphs": [{"text": text, "page": page}
                              for text, page in zip(row.paragraph_texts, row.paragraph_pages)]}
        generated.extend(chunk.chunk_document(doc))
    chunks = pd.DataFrame(generated, columns=CHUNK_COLUMNS)
    chunks_path = tmp_path / "chunks.parquet"
    if missing_chunk:
        chunks = chunks.iloc[1:]
    chunks.to_parquet(chunks_path, index=False)
    return clean.parent, documents, chunks_path


def _run(tmp_path, *, text_mismatch=False, missing_chunk=False):
    lake, documents, chunks = _fixtures(tmp_path, text_mismatch=text_mismatch,
                                        missing_chunk=missing_chunk)
    athena = tmp_path / "10c_athena.json"
    athena.write_text(json.dumps({
        "count_by_partition.sql": {"state": "SUCCEEDED", "row_count": 2,
                                   "rows": [["2020", "minutes", "132"], ["2020", "statement", "134"]]},
        "partition_check.sql": {"state": "SUCCEEDED", "row_count": 7, "rows": []},
    }), encoding="utf-8")
    client = FakeCloudWatch()
    output = tmp_path / "results" / "10c_evidence.json"
    lines = []
    status = evidence.main(
        ["--start", "2026-11-15T00:00:00Z", "--end", "2026-11-15T01:00:00Z"],
        cloudwatch_client=client, lake_root=lake, documents_path=documents,
        chunks_path=chunks, athena_path=athena, output_path=output, report=lines.append,
    )
    return status, json.loads(output.read_text(encoding="utf-8")), client, lines, output


def test_collect_evidence_passes_with_matching_lake_documents_and_chunks(tmp_path):
    status, result, client, lines, output = _run(tmp_path)

    assert status == 0
    assert result["lambda_metrics"]["invocations"] == 7
    assert result["lambda_metrics"]["errors"] == 2
    assert result["lake_text_parity"]["same_doc_ids"] is True
    assert result["d1_chunk_parity"]["status"] == "PASS"
    assert result["athena_counts"]["status"] == "PASS"
    assert result["athena_counts"]["totals_by_doc_type"] == {"minutes": 132, "statement": 134}
    assert len(client.calls) == 2
    assert all(call["Namespace"] == "AWS/Lambda" for call in client.calls)
    assert len(lines) == 4
    assert output.read_bytes().endswith(b"\n")


def test_collect_evidence_fails_on_document_text_mismatch(tmp_path):
    status, result, _client, lines, _output = _run(tmp_path, text_mismatch=True)

    assert status == 1
    assert result["lake_text_parity"]["status"] == "FAIL"
    assert result["lake_text_parity"]["text_mismatch_doc_ids"] == ["statement-20200129"]
    assert any(line.startswith("lake_text_parity: FAIL") for line in lines)


def test_collect_evidence_fails_when_stored_chunk_is_missing(tmp_path):
    status, result, _client, lines, _output = _run(tmp_path, missing_chunk=True)

    assert status == 1
    assert result["d1_chunk_parity"]["status"] == "FAIL"
    assert result["d1_chunk_parity"]["missing_chunk_ids"]
    assert any(line.startswith("d1_chunk_parity: FAIL") for line in lines)

