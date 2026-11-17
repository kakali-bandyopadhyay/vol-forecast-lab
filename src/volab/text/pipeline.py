"""Step 10b pipeline stages: fetch → extract → chunk → dq → index → eval."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from volab.data import REPO_ROOT
from volab.text import chunk, dq, extract, index, kafka_io
from volab.text.fetch_docs import fetch_documents

DEFAULT_SNAPSHOT = "20261112T071104Z"
SNAPSHOT_ROOT = REPO_ROOT / "data" / "snapshots"
TEXT_ROOT = REPO_ROOT / "data" / "text"
RESULTS_TEXT = REPO_ROOT / "results" / "text"
EVAL_QUESTIONS = TEXT_ROOT / "eval_questions.csv"
DOCUMENTS_PATH = TEXT_ROOT / "documents.parquet"
CHUNKS_PATH = TEXT_ROOT / "chunks.parquet"
DQ_PATH = RESULTS_TEXT / "dq_flags.parquet"
MANIFEST_PATH = RESULTS_TEXT / "text_manifest.json"


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _git(args: list[str]):
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True
    )


def require_committed(relative: str) -> None:
    """Refuse an index/eval run unless the eval file is tracked and clean."""
    tracked = _git(["ls-files", "--error-unmatch", relative])
    if tracked.returncode != 0:
        raise SystemExit(f"{relative} is not committed; commit it before indexing")
    dirty = _git(["status", "--porcelain", "--", relative])
    if dirty.stdout.strip():
        raise SystemExit(f"{relative} has uncommitted changes; commit it before indexing")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for piece in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(piece)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")


def _update_manifest(snapshot: str, stage: str, counts: dict[str, Any]) -> None:
    manifest = {}
    if MANIFEST_PATH.is_file():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["snapshot_id"] = snapshot
    docs_manifest = SNAPSHOT_ROOT / snapshot / "docs" / "manifest.json"
    if docs_manifest.is_file():
        manifest["docs_manifest_sha256"] = _sha256(docs_manifest)
    manifest.setdefault("counts", {})[stage] = counts
    manifest["embedding_model"] = index.EMBEDDING_MODEL
    manifest["qdrant_version"] = importlib.metadata.version("qdrant-client")
    manifest["code_commit"] = git_commit()
    _write_json(MANIFEST_PATH, manifest)


DOCUMENT_SCHEMA = pa.schema([
    ("doc_id", pa.string()),
    ("event_date", pa.date32()),
    ("doc_type", pa.string()),
    ("url", pa.string()),
    ("sha256", pa.string()),
    ("text", pa.large_string()),
    ("n_chars", pa.int32()),
    ("page_count", pa.int16()),
    ("votes_for", pa.list_(pa.string())),
    ("votes_against", pa.list_(pa.string())),
    ("dissent_text", pa.string()),
    ("vote_tally", pa.string()),
    ("blocks_dropped", pa.int16()),
    ("extractor_version", pa.string()),
    ("extracted_at", pa.timestamp("us", tz="UTC")),
])

CHUNK_SCHEMA = pa.schema([
    ("chunk_id", pa.string()),
    ("doc_id", pa.string()),
    ("doc_type", pa.string()),
    ("event_date", pa.date32()),
    ("ord", pa.int16()),
    ("page", pa.int16()),
    ("text", pa.large_string()),
    ("n_tokens", pa.int16()),
    ("content_hash", pa.string()),
])

DQ_SCHEMA = pa.schema([
    ("rule", pa.string()),
    ("doc_id", pa.string()),
    ("event_date", pa.date32()),
    ("detail", pa.string()),
    ("severity", pa.string()),
])


def _write_table(frame: pd.DataFrame, path: Path, schema: pa.Schema) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(frame, schema=schema, preserve_index=False)
    pq.write_table(table, path)


def _manifest_files(snapshot: str) -> dict[str, Any]:
    path = SNAPSHOT_ROOT / snapshot / "docs" / "manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"missing docs manifest for snapshot {snapshot}")
    return json.loads(path.read_text(encoding="utf-8")).get("files", {})


def fetch_stage(snapshot: str, *, publish: bool = False) -> dict[str, Any]:
    if publish:  # Codex fix (10c review, 16 Nov)
        files = _manifest_files(snapshot)
    else:
        files = fetch_documents(snapshot).get("files", {})
    counts = {"files": len(files)}
    if publish:
        producer = kafka_io.make_producer()
        published = 0
        for relative, entry in sorted(files.items()):
            if entry.get("status") != "ok":
                continue
            doc_type, filename = relative.split("/", 1)
            compact = Path(filename).stem
            event_date = f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
            message = kafka_io.DocumentMessage(
                doc_id=f"{doc_type}-{compact}",
                doc_type=doc_type,
                event_date=event_date,
                sha256=str(entry.get("sha256", "")),
                path=str(SNAPSHOT_ROOT / snapshot / "docs" / relative),
                s3_key=None,
                url=entry.get("url"),
                event=kafka_io.RAW_EVENT,
            )
            kafka_io.publish_document(message, producer=producer, topic="docs.raw")
            published += 1
        counts["published"] = published
    if not publish:
        _update_manifest(snapshot, "fetch", counts)
    return counts


def extract_stage(snapshot: str) -> dict[str, Any]:
    files = _manifest_files(snapshot)
    existing = {}
    if DOCUMENTS_PATH.is_file():
        frame = pd.read_parquet(DOCUMENTS_PATH)
        existing = {str(row.doc_id): row for row in frame.itertuples(index=False)}

    rows = []
    extracted = 0
    unchanged = 0
    for relative, entry in sorted(files.items()):
        if entry.get("status") != "ok":
            continue
        doc_type, filename = relative.split("/", 1)
        compact = Path(filename).stem
        doc_id = f"{doc_type}-{compact}"
        previous = existing.get(doc_id)
        if (
            previous is not None
            and str(previous.sha256) == str(entry.get("sha256"))
            and str(previous.extractor_version) == extract.EXTRACTOR_VERSION
        ):
            rows.append({key: getattr(previous, key, None) for key in DOCUMENT_SCHEMA.names})
            unchanged += 1
            continue
        source = SNAPSHOT_ROOT / snapshot / "docs" / relative
        record = extract.extract_document(
            source,
            event_date=compact,
            doc_type=doc_type,
            url=str(entry.get("url")),
            sha256=str(entry.get("sha256")),
        )
        rows.append(record)
        extracted += 1

    frame = pd.DataFrame(rows)
    if frame.empty:
        frame = pd.DataFrame(columns=DOCUMENT_SCHEMA.names)
    else:
        frame["event_date"] = pd.to_datetime(frame["event_date"]).dt.date
        frame = frame.sort_values(["event_date", "doc_type"]).reset_index(drop=True)
    _write_table(frame, DOCUMENTS_PATH, DOCUMENT_SCHEMA)
    counts = {"extracted": extracted, "unchanged": unchanged, "total": len(frame)}
    _update_manifest(snapshot, "extract", counts)
    return counts


def chunk_stage(snapshot: str) -> dict[str, Any]:
    documents = pd.read_parquet(DOCUMENTS_PATH)
    files = _manifest_files(snapshot)
    rows = []
    for row in documents.itertuples(index=False):
        compact = row.event_date.strftime("%Y%m%d")
        extension = "html" if row.doc_type == "statement" else "pdf"
        relative = f"{row.doc_type}/{compact}.{extension}"
        entry = files.get(relative, {})
        source = SNAPSHOT_ROOT / snapshot / "docs" / relative
        record = extract.extract_document(
            source,
            event_date=compact,
            doc_type=row.doc_type,
            url=str(entry.get("url", row.url)),
            sha256=str(entry.get("sha256", row.sha256)),
        )
        rows.extend(chunk.chunk_document(record))
    frame = pd.DataFrame(rows)
    if frame.empty:
        frame = pd.DataFrame(columns=CHUNK_SCHEMA.names)
    else:
        frame["event_date"] = pd.to_datetime(frame["event_date"]).dt.date
        frame = frame.sort_values(["event_date", "doc_id", "ord"]).reset_index(drop=True)
    _write_table(frame, CHUNKS_PATH, CHUNK_SCHEMA)
    counts = {"chunks": len(frame), "documents": len(documents)}
    _update_manifest(snapshot, "chunk", counts)
    return counts


def dq_stage(snapshot: str) -> dict[str, Any]:
    flags = dq.check_documents(
        DOCUMENTS_PATH,
        manifest_path=SNAPSHOT_ROOT / snapshot / "docs" / "manifest.json",
        chunks_path=CHUNKS_PATH,
    )
    frame = pd.DataFrame(flags, columns=DQ_SCHEMA.names)
    if not frame.empty:
        frame["event_date"] = pd.to_datetime(frame["event_date"]).dt.date
    _write_table(frame, DQ_PATH, DQ_SCHEMA)
    counts = frame["rule"].value_counts().to_dict() if not frame.empty else {}
    _update_manifest(snapshot, "dq", {str(key): int(value) for key, value in counts.items()})
    return counts


def index_stage(snapshot: str) -> dict[str, Any]:
    require_committed(str(EVAL_QUESTIONS.relative_to(REPO_ROOT)))
    chunks = pd.read_parquet(CHUNKS_PATH)
    documents = pd.read_parquet(DOCUMENTS_PATH)
    counts = index.index_chunks(chunks, documents)
    _update_manifest(snapshot, "index", counts)
    return counts


def _metrics(gold: list[str], retrieved: list[str]) -> dict[str, float]:
    top5 = retrieved[:5]
    top10 = retrieved[:10]
    overlap = len(set(gold) & set(top5))
    recall = overlap / len(gold) if gold else 0.0
    hit = 1.0 if overlap else 0.0
    rank = next((i + 1 for i, chunk_id in enumerate(top10) if chunk_id in set(gold)), None)
    mrr = 1.0 / rank if rank else 0.0
    return {"recall_at_5": recall, "hit_at_5": hit, "mrr_at_10": mrr, "rank": rank or 0}


def _require_index_count(chunks: pd.DataFrame) -> None:
    """Fail before eval if the collection does not contain every chunk."""
    qdrant = index._client(None)
    try:
        count = int(qdrant.count(collection_name=index.COLLECTION, exact=True).count)
    except Exception as exc:
        raise SystemExit(
            f"Index guard: cannot count Qdrant collection {index.COLLECTION!r}; "
            f"run the index stage before eval ({exc})."
        ) from exc
    if count != len(chunks):
        raise SystemExit(
            f"Index guard: Qdrant collection {index.COLLECTION!r} has {count} points "
            f"but chunks.parquet has {len(chunks)} chunks. Run the index stage before eval."
        )


def eval_stage(snapshot: str) -> dict[str, Any]:
    chunks = pd.read_parquet(CHUNKS_PATH)
    _require_index_count(chunks)
    require_committed(str(EVAL_QUESTIONS.relative_to(REPO_ROOT)))
    questions = pd.read_csv(EVAL_QUESTIONS)
    rows = []
    for question in questions.itertuples(index=False):
        gold = [value for value in str(question.gold_chunk_ids).split(";") if value]
        for mode in ("hybrid", "dense", "sparse"):
            results = index.search(question.question, k=10, mode=mode)
            metrics = _metrics(gold, [result["chunk_id"] for result in results])
            rows.append({
                "qid": question.qid,
                "mode": mode,
                "gold_doc_type": question.gold_doc_type,
                **metrics,
            })
    ranks = pd.DataFrame(rows)
    ranks.to_parquet(RESULTS_TEXT / "retrieval_ranks.parquet", index=False)
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
    summary = pd.concat([summary, all_modes], ignore_index=True)
    summary.to_parquet(RESULTS_TEXT / "retrieval_eval.parquet", index=False)
    counts = {"questions": len(questions), "rows": len(ranks)}
    _update_manifest(snapshot, "eval", counts)
    return counts


STAGES = {
    "fetch": fetch_stage,
    "extract": extract_stage,
    "chunk": chunk_stage,
    "dq": dq_stage,
    "index": index_stage,
    "eval": eval_stage,
}


def run_stage(stage: str, snapshot: str = DEFAULT_SNAPSHOT, *, publish: bool = False) -> dict[str, Any]:
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage}")
    if stage == "fetch":
        return fetch_stage(snapshot, publish=publish)
    return STAGES[stage](snapshot)

