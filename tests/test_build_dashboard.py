import hashlib
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest


SCRIPT = Path(__file__).parents[1] / "scripts/build_dashboard.py"
SPEC = importlib.util.spec_from_file_location("build_dashboard", SCRIPT)
dashboard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dashboard)


def _inputs(root: Path):
    (root / "results/text").mkdir(parents=True)
    (root / "results").mkdir(exist_ok=True)
    (root / "results/text/text_manifest.json").write_text(json.dumps({
        "counts": {"extract": {"total": 3}, "chunk": {"chunks": 12},
                   "index": {"tokens": 0}, "eval": {"questions": 2}}}), encoding="utf-8")
    pd.DataFrame([{"rule": "missing", "severity": "info"}]).to_parquet(root / "results/text/dq_flags.parquet")
    pd.DataFrame([
        {"mode": "dense", "gold_doc_type": "all", "n": 2, "recall_at_5": .5, "mrr_at_10": .4},
        {"mode": "dense", "gold_doc_type": "statement", "n": 1, "recall_at_5": 1., "mrr_at_10": .8},
        {"mode": "hybrid", "gold_doc_type": "all", "n": 2, "recall_at_5": .25, "mrr_at_10": .2},
    ]).to_parquet(root / "results/text/retrieval_eval.parquet")
    pd.DataFrame([{"model": "M1", "qlike": 2.}, {"model": "M2", "qlike": 1.}]).to_parquet(root / "results/losses.parquet")
    pd.DataFrame([{"test": "clark_west", "pair": "M2_vs_M1", "statistic": 1., "pvalue": .2, "family": "headline"}]).to_parquet(root / "results/tests.parquet")
    pd.DataFrame([{"test": "clark_west", "pair": "M2_vs_M1", "statistic": 2., "pvalue": .03, "family": "headline"}]).to_parquet(root / "results/tests_ex_window.parquet")
    pd.DataFrame([{"pair": "M2_vs_M1", "sample": "full", "statistic": 1., "holm_p": .4, "decision": "not rejected"}]).to_parquet(root / "results/holm_final.parquet")
    pd.DataFrame([
        {"test": "diebold_mariano", "pair": "M4_vs_M2", "sample": "full", "statistic": -0.2, "pvalue": .8, "family": "Q1c"},
        {"test": "diebold_mariano", "pair": "M4_vs_M2", "sample": "ex_window", "statistic": -1.9, "pvalue": .06, "family": "Q1c"},
    ]).to_parquet(root / "results/mlp_tests.parquet")
    pd.DataFrame([{"sample": "full", "regime": "low", "n": 2, "mean_qlike_m2": 1.0,
                   "mean_qlike_m4": 0.8, "difference_m4_minus_m2": -0.2}]).to_parquet(
        root / "results/mlp_regimes.parquet")
    pd.DataFrame(columns=["variable", "sample", "group", "label", "n", "mean", "median", "share_positive", "hac_t", "p_two_sided"]).to_parquet(root / "results/q2_summary.parquet")
    (root / "data/text").mkdir(parents=True)
    documents = ([{"doc_type": "statement", "event_date": f"2010-{(i % 12) + 1:02d}-01"} for i in range(134)]
                 + [{"doc_type": "minutes", "event_date": f"2011-{(i % 12) + 1:02d}-01"} for i in range(132)])
    pd.DataFrame(documents).to_parquet(root / "data/text/documents.parquet")
    token_counts = [227] * 4124 + [226] * (6620 - 4124)
    pd.DataFrame({"doc_type": ["statement"] * 365 + ["minutes"] * (6620 - 365),
                  "n_tokens": token_counts}).to_parquet(root / "data/text/chunks.parquet")
    questions = [{"qid": f"q{i:02d}", "question": f"Question {i} " + ("x" * 90),
                  "gold_doc_type": "statement" if i <= 8 else "minutes",
                  "gold_chunk_ids": "private", "answer": "private"} for i in range(1, 16)]
    pd.DataFrame(questions).to_csv(root / "data/text/eval_questions.csv", index=False)
    pd.DataFrame([{"qid": q["qid"], "mode": mode, "hit_at_5": (i + len(mode)) % 2}
                  for i, q in enumerate(questions) for mode in ["hybrid", "dense", "sparse"]]
                 ).to_parquet(root / "results/text/retrieval_ranks.parquet")
    pd.DataFrame([
        {"mode": "fused_os", "gold_doc_type": "all", "n": 15, "recall_at_5": .6,
         "hit_at_5": .7, "mrr_at_10": .5}
    ]).to_parquet(root / "results/text/retrieval_eval_10c.parquet")


def test_build_corpus_totals_grid_and_provenance(tmp_path):
    _inputs(tmp_path)
    out = tmp_path / "site/dashboard"
    data = dashboard.build(tmp_path, out)
    saved = json.loads((out / "data.json").read_text(encoding="utf-8"))
    assert saved == data
    bound_method_marker = "<bound " + "method"
    assert bound_method_marker not in json.dumps(saved)
    assert data["schema_version"] == 1
    assert data["corpus"]["documents"] == 266
    assert data["corpus"]["chunks"] == 6620
    assert data["corpus"]["tokens"] == 1_500_244
    assert data["corpus"]["documents_by_doc_type_and_year"]["by_doc_type"] == [
        {"doc_type": "minutes", "documents": 132}, {"doc_type": "statement", "documents": 134}]
    assert len(data["corpus"]["documents_by_doc_type_and_year"]["by_year"]) == 2
    assert data["retrieval"]["best_mode"] == "fused (10c)"
    assert data["retrieval"]["best_recall_at_5"] == .6
    assert data["retrieval"]["release_comparison"] == {
        "10b_dense": {"label": "10b dense", "recall_at_5": .5},
        "10c_fused": {"label": "10c fused", "recall_at_5": .6}}
    assert data["platform_evidence"]["status"] == "available"
    assert "retrieval_eval_10c.parquet" in data["platform_evidence"]["files"]
    grid = data["retrieval"]["question_grid"]
    assert grid["available"] is True
    assert grid["modes"] == ["dense", "hybrid", "sparse"]
    assert len(grid["rows"]) == 15
    assert set(grid["rows"][0]) == {"qid", "label", "gold_doc_type", "hits"}
    assert len(grid["rows"][0]["label"]) == 80
    assert set(grid["rows"][0]["hits"]) == set(grid["modes"])
    assert data["forecasting"]["model_mapping_source"].startswith("prereg/PREREGISTRATION.md")
    assert {row["id"]: row["name"] for row in data["forecasting"]["model_mapping"]}["M4"] == "MLP"
    assert "M4" in {row["model"] for row in data["forecasting"]["loss_by_model_horizon"]}
    cw = data["forecasting"]["clark_west"]
    assert [(row["sample"], row["headline"]) for row in cw[:2]] == [
        ("ex_window", True), ("full", False)]
    assert any(row["pair"] == "M4_vs_M2" for row in data["forecasting"]["model_tests"])
    for entry in data["provenance"]:
        assert entry["sha256"] == hashlib.sha256((tmp_path / entry["file"]).read_bytes()).hexdigest()


def test_licence_guard_rejects_forbidden_key_and_long_series():
    with pytest.raises(ValueError, match="exceeds 50"):
        dashboard.assert_no_market_series({"price_levels": [1] * 51})
    with pytest.raises(ValueError, match="exceeds 50"):
        dashboard.assert_no_market_series({"forecast_series": [0] * 51})


def test_build_fails_when_corpus_totals_differ(tmp_path):
    _inputs(tmp_path)
    pd.DataFrame({"doc_type": ["statement"], "n_tokens": [1]}).to_parquet(
        tmp_path / "data/text/chunks.parquet")
    with pytest.raises(ValueError, match="Expected 6620 chunks"):
        dashboard.build(tmp_path, tmp_path / "site/dashboard")


def test_platform_evidence_includes_present_optional_files(tmp_path):
    (tmp_path / "results/text").mkdir(parents=True)
    (tmp_path / "results/text/10c_kafka.json").write_text('{"processed": 2}', encoding="utf-8")
    sources = []
    result = dashboard._platform(tmp_path, sources)
    assert result["status"] == "available"
    assert result["files"]["10c_kafka.json"] == {"processed": 2}
    assert sources == ["results/text/10c_kafka.json"]
