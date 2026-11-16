"""Offline tests for the temporary BM25 diagnostic helpers."""

from __future__ import annotations

from pathlib import Path

from scripts import run_bm25_ablation as ablation
from scripts import run_eval_10c


def test_ablation_imports_real_package_and_run_eval_dependency_exists():
    assert callable(ablation.run_ablation)
    assert callable(run_eval_10c._filters)



def test_query_variant_builder_sets_header_boost_and_analyzer():
    boosted = ablation.build_query("rates", header_boost=True, analyzer="english")
    plain = ablation.build_query("rates", header_boost=False, analyzer="standard")

    assert boosted["query"]["bool"]["must"][0]["multi_match"] == {
        "query": "rates", "fields": ["header^2", "text"], "analyzer": "english",
    }
    assert plain["query"]["bool"]["must"][0]["multi_match"]["fields"] == ["header", "text"]
    assert plain["query"]["bool"]["must"][0]["multi_match"]["analyzer"] == "standard"


def test_cleanup_deletes_only_temporary_indexes():
    class FakeIndices:
        def __init__(self):
            self.names = {"fomc_chunks_abl_english", "fomc_chunks_abl_standard", "fomc_chunks_v1"}
            self.deleted = []

        def exists(self, *, index):
            return index in self.names

        def delete(self, *, index):
            self.deleted.append(index)
            self.names.remove(index)

    class FakeClient:
        indices = FakeIndices()

    client = FakeClient()
    ablation.cleanup_indexes(client, ["fomc_chunks_abl_english", "fomc_chunks_abl_standard"])
    assert client.indices.deleted == ["fomc_chunks_abl_english", "fomc_chunks_abl_standard"]
    assert client.indices.names == {"fomc_chunks_v1"}
