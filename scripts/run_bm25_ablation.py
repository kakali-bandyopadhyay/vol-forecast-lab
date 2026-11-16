"""POST-HOC DIAGNOSTIC: compare temporary BM25 variants; never query or modify fomc_chunks_v1."""

from __future__ import annotations

import json
import copy
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
CHUNKS = REPO_ROOT / "data" / "text" / "chunks.parquet"
QUESTIONS = REPO_ROOT / "data" / "text" / "eval_questions.csv"
OUTPUT = REPO_ROOT / "results" / "text" / "10c_bm25_ablation.json"
INDEX_CONFIGS = (
    ("fomc_chunks_abl_english", True, "english"),
    ("fomc_chunks_abl_standard", False, "standard"),
)


def build_query(query: str, *, header_boost: bool, analyzer: str, **filters) -> dict:
    """Build one of the four post-hoc query variants."""
    from volab.text.opensearch_index import bm25_query

    body = bm25_query(query, **filters)
    fields = ["header^2", "text"] if header_boost else ["header", "text"]
    body["query"]["bool"]["must"] = [{"multi_match": {
        "query": query, "fields": fields, "analyzer": analyzer,
    }}]
    return body


def cleanup_indexes(client, names: tuple[str, ...] | list[str]) -> None:
    """Delete only the named temporary ablation indexes, if present."""
    for name in names:
        if client.indices.exists(index=name):
            client.indices.delete(index=name)


def _build_indexes(client, chunks: pd.DataFrame) -> None:
    from volab.text import opensearch_index as os_index

    for name, _boost, analyzer in INDEX_CONFIGS:
        mapping = copy.deepcopy(os_index.MAPPING)
        mapping["mappings"]["properties"]["header"]["analyzer"] = analyzer
        mapping["mappings"]["properties"]["text"]["analyzer"] = analyzer
        client.indices.create(index=name, body=mapping)
        body = []
        for row in chunks.itertuples(index=False):
            body.append({"index": {"_index": name, "_id": str(row.chunk_id)}})
            body.append(os_index._document(row))
        if body:
            response = client.bulk(body=body, refresh="wait_for")
            if response.get("errors"):
                raise RuntimeError(f"failed to build temporary index {name}")


def run_ablation(*, client=None, chunks_path: Path = CHUNKS,
                 questions_path: Path = QUESTIONS, output_path: Path = OUTPUT) -> dict:
    """Run four post-hoc variants against temporary indexes and clean them in finally."""
    if client is None:
        from opensearchpy import OpenSearch

        client = OpenSearch(hosts=[{"host": "127.0.0.1", "port": 9200}], use_ssl=False, timeout=30)
    chunks = pd.read_parquet(chunks_path)
    questions = pd.read_csv(questions_path)
    names = tuple(name for name, _boost, _analyzer in INDEX_CONFIGS)
    try:
        _build_indexes(client, chunks)
        from scripts.run_eval_10c import _filters
        from volab.text.pipeline import _metrics

        scores = {f"header{int(boost)}_{analyzer}": [] for _name, _index_boost, analyzer in INDEX_CONFIGS
                  for boost in (True, False)}
        # Two independently built indexes keep analyzer choice isolated; query boost varies per run.
        for row in questions.to_dict(orient="records"):
            query = str(row["question"])
            filters = _filters(pd.Series(row))
            gold = [value for value in str(row["gold_chunk_ids"]).split(";") if value]
            for name, _index_boost, analyzer in INDEX_CONFIGS:
                for boost in (True, False):
                    key = f"header{int(boost)}_{analyzer}"
                    response = client.search(index=name, body=build_query(
                        query, header_boost=boost, analyzer=analyzer, **filters,
                    ))
                    retrieved = [str(hit.get("_source", {}).get("chunk_id", hit.get("_id", "")))
                                 for hit in response.get("hits", {}).get("hits", [])[:50]]
                    scores[key].append(_metrics(gold, retrieved))
        result = {
            "diagnostic": "POST-HOC DIAGNOSTIC; not a preregistered evaluation",
            "n_questions": len(questions),
            "variants": {
                key: {
                    metric: sum(item[metric] for item in rows) / len(rows) if rows else 0.0
                    for metric in ("recall_at_5", "hit_at_5", "mrr_at_10")
                }
                for key, rows in scores.items()
            },
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
        print("POST-HOC DIAGNOSTIC")
        for variant, metrics in result["variants"].items():
            print(variant, metrics)
        return result
    finally:
        cleanup_indexes(client, names)


if __name__ == "__main__":
    run_ablation()
