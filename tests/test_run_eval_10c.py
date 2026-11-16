"""Offline tests for the 10c retrieval evaluation plumbing."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_eval_10c.py"
SPEC = importlib.util.spec_from_file_location("run_eval_10c", SCRIPT)
run_eval_10c = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(run_eval_10c)


def test_eval_uses_fake_search_functions_and_writes_10b_summary_columns(tmp_path):
    questions = tmp_path / "questions.csv"
    pd.DataFrame([{
        "qid": "q1", "question": "target range", "gold_doc_type": "statement",
        "gold_chunk_ids": "a;b", "answer": "target range maintained",
        "date_from": "2000-01-01", "date_to": "2001-01-01", "doc_type": "statement",
    }]).to_csv(questions, index=False)
    calls = {"bm25": [], "dense": []}

    def fake_bm25(query, **filters):
        calls["bm25"].append((query, filters))
        return ["a", "x"]

    def fake_dense(query, *, k, mode, **filters):
        calls["dense"].append((query, k, mode, filters))
        return [{"chunk_id": "b"}, {"chunk_id": "a"}]

    def fake_rrf(rankings, *, k):
        assert rankings == [["a", "x"], ["b", "a"]]
        assert k == 60
        return [("a", 1.0), ("b", 0.5), ("x", 0.25)]

    metric_calls = []

    def fake_metrics(gold, retrieved):
        metric_calls.append((gold, retrieved))
        return {"recall_at_5": 0.5, "hit_at_5": 1.0, "mrr_at_10": 1.0, "rank": 1}

    destination = tmp_path / "out" / "retrieval_eval_10c.parquet"
    chunks = pd.DataFrame([{"chunk_id": "a"}, {"chunk_id": "b"}])
    run_eval_10c.CHUNKS = tmp_path / "chunks.parquet"
    chunks.to_parquet(run_eval_10c.CHUNKS, index=False)

    class FakeOpenSearch:
        def count(self, *, index):
            assert index == "fomc_chunks_v1"
            return {"count": 2}

    result = run_eval_10c.run_eval(
        questions, destination, bm25_search=fake_bm25, dense_search=fake_dense,
        fuse=fake_rrf, metrics_func=fake_metrics, opensearch_client=FakeOpenSearch(),
        qdrant_count_check=lambda frame: None,
    )

    assert destination.exists()
    assert set(result.columns) == {
        "mode", "gold_doc_type", "n", "recall_at_5", "hit_at_5", "mrr_at_10"
    }
    assert set(result["mode"]) == {"bm25_os", "dense", "fused_os"}
    assert calls["bm25"] == [("target range", {
        "date_from": "2000-01-01", "date_to": "2001-01-01", "doc_type": "statement"
    })]
    assert calls["dense"] == [("target range", 50, "dense", {
        "date_from": "2000-01-01", "date_to": "2001-01-01", "doc_type": "statement"
    })]
    assert metric_calls == [
        (["a", "b"], ["a", "x"]),
        (["a", "b"], ["b", "a"]),
        (["a", "b"], ["a", "b", "x"]),
    ]
    assert result.loc[result["mode"] == "bm25_os", "recall_at_5"].iloc[0] == 0.5


def test_opensearch_count_guard_fails_before_any_search(tmp_path):
    chunks_path = tmp_path / "chunks.parquet"
    pd.DataFrame([{"chunk_id": "one"}, {"chunk_id": "two"}]).to_parquet(chunks_path, index=False)
    run_eval_10c.CHUNKS = chunks_path

    class WrongCountClient:
        def count(self, *, index):
            return {"count": 1}

    calls = []
    with pytest.raises(SystemExit, match="OpenSearch index.*has 1 documents"):
        run_eval_10c.run_eval(
            tmp_path / "missing_questions.csv", tmp_path / "unused.parquet",
            bm25_search=lambda *args, **kwargs: calls.append("query"),
            dense_search=lambda *args, **kwargs: calls.append("query"),
            fuse=lambda *args, **kwargs: [], metrics_func=lambda *args: {},
            opensearch_client=WrongCountClient(),
            qdrant_count_check=lambda frame: calls.append("qdrant"),
        )
    assert calls == []


def test_qdrant_count_guard_fails_before_any_search(tmp_path):
    chunks_path = tmp_path / "chunks.parquet"
    pd.DataFrame([{"chunk_id": "one"}]).to_parquet(chunks_path, index=False)
    run_eval_10c.CHUNKS = chunks_path

    class CorrectCountClient:
        def count(self, *, index):
            return {"count": 1}

    calls = []

    def bad_qdrant_count(frame):
        raise SystemExit("Index guard: Qdrant collection has 0 points but chunks.parquet has 1 chunks.")

    with pytest.raises(SystemExit, match="Qdrant collection"):
        run_eval_10c.run_eval(
            tmp_path / "missing_questions.csv", tmp_path / "unused.parquet",
            bm25_search=lambda *args, **kwargs: calls.append("query"),
            dense_search=lambda *args, **kwargs: calls.append("query"),
            fuse=lambda *args, **kwargs: [], metrics_func=lambda *args: {},
            opensearch_client=CorrectCountClient(), qdrant_count_check=bad_qdrant_count,
        )
    assert calls == []
