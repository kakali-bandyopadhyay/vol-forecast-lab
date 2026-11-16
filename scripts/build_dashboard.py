"""Build the static, licence-safe GitHub Pages dashboard from committed results."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    "results/text/text_manifest.json",
    "results/text/dq_flags.parquet",
    "results/text/retrieval_eval.parquet",
    "results/losses.parquet",
    "results/tests.parquet",
    "results/holm_final.parquet",
    "results/mlp_tests.parquet",
    "results/q2_summary.parquet",
]
OPTIONAL = [
    "results/text/10c_kafka.json",
    "results/text/10c_athena.json",
    "results/text/10c_evidence.json",
    "results/text/retrieval_eval_10c.parquet",
]
CORPUS_INPUTS = [
    "data/text/documents.parquet",
    "data/text/chunks.parquet",
    "data/text/eval_questions.csv",
    "results/text/retrieval_ranks.parquet",
]
EXPECTED_DOCS = {"total": 266, "statement": 134, "minutes": 132}
EXPECTED_CHUNKS = 6620
EXPECTED_TOKENS = 1_500_244
MODEL_MAPPING = [
    {"id": "M0", "name": "Persistence baseline"},
    {"id": "M1", "name": "HAR"},
    {"id": "M2", "name": "HAR-X"},
    {"id": "M3", "name": "GARCH"},
    {"id": "M4", "name": "MLP"},
]
MODEL_MAPPING_SOURCE = "prereg/PREREGISTRATION.md, section 4.2 model table (M1 through M4); M0 is the persistence baseline in scripts/run_eval.py."
FORBIDDEN = re.compile(r"(?:price|close|return|rv_|vix|forecast)", re.I)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _scalar(value: Any) -> Any:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _df(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)


def _metrics(rows: pd.DataFrame, by: list[str]) -> list[dict[str, Any]]:
    return [
        {**{key: _scalar(row[key]) for key in by},
         "n": int(row["n"]), "recall_at_5": float(row["recall_at_5"]),
         "mrr_at_10": float(row["mrr_at_10"])}
        for _, row in rows.iterrows()
    ]


def _forecasting(root: Path) -> dict[str, Any]:
    losses = _df(root / "results/losses.parquet")
    means = losses.groupby("model", as_index=False)["qlike"].mean()
    har_row = means.loc[means.model == "M1", "qlike"]
    har = float(har_row.iloc[0]) if not har_row.empty else None
    # The committed losses table has no horizon column. It evaluates the 30-day
    # realised-volatility target; preserve that target label without row data.
    loss_summary = []
    for row in means.to_dict("records"):
        avg = float(row["qlike"])
        loss_summary.append({"model": str(row["model"]), "horizon": "30d",
                             "mean_loss": avg,
                             "loss_ratio_vs_har": avg / har if har else None})

    tests = _df(root / "results/tests.parquet")
    holm = _df(root / "results/holm_final.parquet")
    mlp = _df(root / "results/mlp_tests.parquet")
    q2 = _df(root / "results/q2_summary.parquet")
    cw: list[dict[str, Any]] = []
    for _, row in tests.loc[tests.test == "clark_west"].iterrows():
        cw.append({"pair": str(row.pair), "statistic": _scalar(row.statistic),
                   "pvalue": _scalar(row.pvalue), "sample": "full",
                   "source": "tests.parquet"})
    for _, row in mlp.loc[mlp.test == "clark_west"].iterrows():
        cw.append({"pair": str(row.pair), "statistic": _scalar(row.statistic),
                   "pvalue": _scalar(row.pvalue), "sample": str(row.get("sample", "full")),
                   "source": "mlp_tests.parquet"})
    holm_rows = [{"pair": str(row.pair), "sample": str(row.sample),
                  "statistic": _scalar(row.statistic), "holm_p": _scalar(row.holm_p),
                  "reject": str(row.decision).lower() == "reject",
                  "decision": str(row.decision)} for _, row in holm.iterrows()]
    return {"loss_by_model_horizon": loss_summary, "clark_west": cw,
            "holm": holm_rows,
            "available_columns": {
                "losses.parquet": list(losses.columns),
                "tests.parquet": list(tests.columns),
                "holm_final.parquet": list(holm.columns),
                "mlp_tests.parquet": list(mlp.columns),
                "q2_summary.parquet": list(q2.columns),
            },
            "model_mapping": MODEL_MAPPING,
            "model_mapping_source": MODEL_MAPPING_SOURCE,
            "note": "Horizon is shown as 30d from the target definition; loss input has no horizon column."}


def _corpus_breakdown(root: Path) -> tuple[dict[str, Any], int, int, int]:
    """Read only the explicitly allowed aggregate columns; never load document or chunk text."""
    docs = pd.read_parquet(root / "data/text/documents.parquet", columns=["doc_type", "event_date"])
    chunks = pd.read_parquet(root / "data/text/chunks.parquet", columns=["doc_type", "n_tokens"])
    doc_types = docs["doc_type"].value_counts().to_dict()
    if len(docs) != EXPECTED_DOCS["total"] or any(
            int(doc_types.get(kind, 0)) != count
            for kind, count in EXPECTED_DOCS.items() if kind != "total"):
        raise ValueError(f"Expected 266 documents (134 statement, 132 minutes), got {len(docs)} {doc_types}")
    if set(doc_types) != {"statement", "minutes"}:
        raise ValueError(f"Unexpected document types in corpus: {doc_types}")
    if len(chunks) != EXPECTED_CHUNKS:
        raise ValueError(f"Expected {EXPECTED_CHUNKS} chunks, got {len(chunks)}")
    token_total = int(chunks["n_tokens"].sum())
    if token_total != EXPECTED_TOKENS:
        raise ValueError(f"Expected {EXPECTED_TOKENS} corpus tokens, got {token_total}")

    year_rows = (docs.assign(year=pd.to_datetime(docs["event_date"]).dt.year.astype(str))
                 .groupby(["doc_type", "year"], sort=True).size().reset_index(name="documents"))
    chunks_by_type = chunks.groupby("doc_type", sort=True).size()
    breakdown = {
        "by_doc_type": [{"doc_type": str(kind), "documents": int(count)}
                        for kind, count in sorted(doc_types.items())],
        "by_year": [{"doc_type": str(row.doc_type), "year": str(row.year),
                     "documents": int(row.documents)} for row in year_rows.itertuples(index=False)],
        "chunks_by_doc_type": [{"doc_type": str(kind), "chunks": int(count)}
                               for kind, count in chunks_by_type.items()],
    }
    return breakdown, len(docs), len(chunks), token_total


def _question_grid(root: Path) -> tuple[dict[str, Any], int]:
    """Join only question labels and hit@5 metrics; omit gold IDs, answers, ranks and chunk text."""
    questions = pd.read_csv(root / "data/text/eval_questions.csv", usecols=["qid", "question", "gold_doc_type"])
    ranks = pd.read_parquet(root / "results/text/retrieval_ranks.parquet",
                            columns=["qid", "mode", "hit_at_5"])
    modes = sorted(ranks["mode"].dropna().astype(str).unique())
    hit_rows = ranks.pivot(index="qid", columns="mode", values="hit_at_5")
    if questions["qid"].duplicated().any() or set(questions["qid"]) != set(hit_rows.index):
        raise ValueError("Eval questions and retrieval ranks do not have the same unique question IDs")
    if hit_rows[modes].isna().any().any():
        raise ValueError("Retrieval hit@5 grid has missing question/mode entries")
    rows = []
    for question in questions.to_dict("records"):
        hits = hit_rows.loc[question["qid"]]
        rows.append({"qid": str(question["qid"]), "label": str(question["question"])[:80],
                     "gold_doc_type": str(question["gold_doc_type"]),
                     "hits": {mode: bool(hits[mode]) for mode in modes}})
    return {"available": True, "modes": modes, "rows": rows}, len(questions)


def _platform(root: Path, sources: list[str]) -> dict[str, Any]:
    existing = [p for p in OPTIONAL if (root / p).exists()]
    if not existing:
        return {"status": "pending", "message": "Pending AWS evidence run"}
    payload: dict[str, Any] = {"status": "available", "files": {}}
    for rel in existing:
        path = root / rel
        sources.append(rel)
        if path.suffix == ".json":
            payload["files"][Path(rel).name] = _read_json(path)
        else:
            frame = _df(path)
            payload["files"][Path(rel).name] = {
                "rows": int(len(frame)), "columns": list(frame.columns),
                "records": frame.to_dict("records")}
    return payload


def build(root: Path = ROOT, output: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    out = output or root / "site/dashboard"
    out.mkdir(parents=True, exist_ok=True)
    required_sources = SOURCES + CORPUS_INPUTS
    present = [rel for rel in required_sources if (root / rel).exists()]
    missing = [rel for rel in required_sources if rel not in present]
    if missing:
        raise FileNotFoundError(f"Required dashboard inputs missing: {', '.join(missing)}")

    manifest = _read_json(root / SOURCES[0])
    dq = _df(root / SOURCES[1])
    retrieval = _df(root / SOURCES[2])
    overall = retrieval.loc[retrieval.gold_doc_type.astype(str).str.lower() == "all"]
    if overall.empty:
        overall = retrieval.groupby("mode", as_index=False).agg(
            n=("n", "sum"), recall_at_5=("recall_at_5", "mean"),
            mrr_at_10=("mrr_at_10", "mean"))
    best = overall.loc[overall.recall_at_5.idxmax()] if not overall.empty else None
    counts = manifest.get("counts", {})
    corpus_counts = counts.get("extract", {})
    chunk_counts = counts.get("chunk", {})
    docs_by_type_year, doc_count, chunks, token_count = _corpus_breakdown(root)
    question_grid, question_count = _question_grid(root)

    sources = present.copy()
    platform = _platform(root, sources)
    provenance = []
    for rel in sources:
        path = root / rel
        provenance.append({"file": rel.replace("\\", "/"),
                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                                check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unavailable"

    payload = {
        "schema_version": 1,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": commit,
        "provenance": provenance,
        "corpus": {
            "documents": doc_count, "chunks": chunks, "tokens": token_count,
            "documents_by_doc_type_and_year": docs_by_type_year,
            "tokens_note": f"Corpus token total: {token_count:,} (sum of chunks.parquet n_tokens).",
            "dq_flags": {"count": int(len(dq)), "by_reason": [
                {"reason": str(k), "count": int(v)}
                for k, v in dq.groupby("rule", dropna=False).size().items()]},
            "dq_severity": [{"severity": str(k), "count": int(v)}
                            for k, v in dq.groupby("severity", dropna=False).size().items()],
        },
        "retrieval": {
            "overall": _metrics(overall, ["mode"]),
            "by_doc_type": _metrics(retrieval.loc[
                retrieval.gold_doc_type.astype(str).str.lower() != "all"],
                ["mode", "gold_doc_type"]),
            "question_grid": question_grid,
            "best_mode": str(best["mode"]) if best is not None else None,
            "best_recall_at_5": float(best["recall_at_5"]) if best is not None else None,
            "question_count": question_count,
        },
        "forecasting": _forecasting(root),
        "platform_evidence": platform,
    }
    assert_no_market_series(payload)
    (out / "data.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                                   encoding="utf-8", newline="\n")
    diagram = root / "figures/diagrams/platform.svg"
    if diagram.exists() and (not (out / "platform.svg").exists()
                             or (out / "platform.svg").read_bytes() != diagram.read_bytes()):
        (out / "platform.svg").write_bytes(diagram.read_bytes())
    return payload


def assert_no_market_series(value: Any, path: str = "data") -> None:
    """Reject market-like keys and any long numeric series in exported JSON."""
    if isinstance(value, dict):
        for key, child in value.items():
            if FORBIDDEN.search(str(key)) and isinstance(child, (list, tuple)) and len(child) > 50:
                raise ValueError(f"Market-like series array exceeds 50 values at {path}.{key}")
            if FORBIDDEN.search(str(key)) and isinstance(child, (list, tuple)) and len(child) > 50:
                raise ValueError(f"Market-like series array exceeds 50 values at {path}.{key}")
            assert_no_market_series(child, f"{path}.{key}")
    elif isinstance(value, list):
        for i, child in enumerate(value):
            assert_no_market_series(child, f"{path}[{i}]")


if __name__ == "__main__":
    result = build()
    print(f"Built {ROOT / 'site/dashboard/data.json'} ({len(result['provenance'])} provenance files)")
    corpus = result["corpus"]
    docs = corpus["documents_by_doc_type_and_year"]["by_doc_type"]
    doc_counts = ", ".join(f"{row['documents']} {row['doc_type']}" for row in docs)
    print(f"Corpus: {corpus['documents']} documents ({doc_counts}), {corpus['chunks']:,} chunks, {corpus['tokens']:,} tokens")
    grid = result["retrieval"]["question_grid"]
    print(f"Question grid: {len(grid['rows'])} questions x {len(grid['modes'])} modes")
    print(f"Model mapping source: {result['forecasting']['model_mapping_source']}")
