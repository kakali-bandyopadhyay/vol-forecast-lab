"""Rerun the committed Step 10b retrieval questions against 10c search modes."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

QUESTIONS = REPO_ROOT / "data" / "text" / "eval_questions.csv"
OUTPUT = REPO_ROOT / "results" / "text" / "retrieval_eval_10c.parquet"


def _filters(question: pd.Series) -> dict[str, str]:
    """Use filter columns if supplied; the committed 10b file has none."""
    return {
        name: str(question[name])
        for name in ("date_from", "date_to", "doc_type")
        if name in question.index and pd.notna(question[name]) and str(question[name]).strip()
    }


def run_eval(
    question_path: Path = QUESTIONS,
    output_path: Path = OUTPUT,
    *,
    bm25_search: Callable | None = None,
    dense_search: Callable | None = None,
    fuse: Callable | None = None,
    metrics_func: Callable | None = None,
) -> pd.DataFrame:
    """Evaluate BM25, dense and fused top-50 rankings and write the 10b summary shape."""
    if bm25_search is None or fuse is None:
        from opensearchpy import OpenSearch
        from volab.text.opensearch_index import rrf as default_rrf
        from volab.text.opensearch_index import search_bm25 as default_bm25

        if bm25_search is None:
            os_client = OpenSearch(hosts=[{"host": "127.0.0.1", "port": 9200}], use_ssl=False, timeout=30)
            bm25_search = lambda query, **filters: default_bm25(os_client, query, **filters)
        fuse = fuse or default_rrf
    if dense_search is None:
        from volab.text import index

        dense_search = index.search
    if metrics_func is None:
        # Reuse the Step 10b metric implementation; do not let the test path
        # import its tokenizer-heavy pipeline module unless it needs the real metrics.
        from volab.text.pipeline import _metrics as metrics_func
    questions = pd.read_csv(question_path)
    rows: list[dict] = []

    for question in questions.to_dict(orient="records"):
        filters = _filters(pd.Series(question))
        query = str(question["question"])
        bm25_ids = list(bm25_search(query, **filters))[:50]
        dense_results = dense_search(query, k=50, mode="dense", **filters)
        dense_ids = [item["chunk_id"] if isinstance(item, dict) else str(item) for item in dense_results][:50]
        rankings = {
            "bm25_os": bm25_ids,
            "dense": dense_ids,
            "fused_os": [chunk_id for chunk_id, _score in fuse([bm25_ids, dense_ids], k=60)][:50],
        }
        gold = [value for value in str(question["gold_chunk_ids"]).split(";") if value]
        for mode, retrieved in rankings.items():
            rows.append({
                "qid": question["qid"],
                "mode": mode,
                "gold_doc_type": question["gold_doc_type"],
                **metrics_func(gold, retrieved),
            })

    ranks = pd.DataFrame(rows)
    summary = (
        ranks.groupby(["mode", "gold_doc_type"], dropna=False)
        .agg(
            n=("qid", "size"),
            recall_at_5=("recall_at_5", "mean"),
            hit_at_5=("hit_at_5", "mean"),
            mrr_at_10=("mrr_at_10", "mean"),
        )
        .reset_index()
    )
    all_modes = (
        ranks.groupby("mode")
        .agg(
            n=("qid", "size"),
            recall_at_5=("recall_at_5", "mean"),
            hit_at_5=("hit_at_5", "mean"),
            mrr_at_10=("mrr_at_10", "mean"),
        )
        .reset_index()
    )
    all_modes["gold_doc_type"] = "all"
    output = pd.concat([summary, all_modes], ignore_index=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output.to_parquet(output_path, index=False)
    return output


if __name__ == "__main__":
    result = run_eval()
    print(result.to_string(index=False))
    print(f"written: {OUTPUT}")
