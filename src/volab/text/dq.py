"""Data-quality flags for the Step 10b document/chunk corpus."""

from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from volab.data import REPO_ROOT
from volab.text.extract import EXTRACTOR_VERSION

DOCUMENTS_PATH = REPO_ROOT / "data" / "text" / "documents.parquet"
CHUNKS_PATH = REPO_ROOT / "data" / "text" / "chunks.parquet"
EVENTS_FILE = REPO_ROOT / "data" / "events" / "fomc.csv"

MINUTES_RELEASE_GRACE_DAYS = 35
STATEMENT_MIN_CHARS = 500
MINUTES_MIN_CHARS = 15_000


def _scheduled_dates(path: Path) -> list[date]:
    dates = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            dates.append(datetime.strptime(row["event_date"].strip(), "%Y-%m-%d").date())
    return dates


def _flag(rule: str, *, event_date: date | None = None, doc_id: str | None = None,
          detail: str, severity: str = "failure") -> dict[str, Any]:
    return {
        "rule": rule,
        "doc_id": doc_id,
        "event_date": event_date,
        "detail": detail,
        "severity": severity,
    }


def check_documents(
    documents_path: Path = DOCUMENTS_PATH,
    *,
    manifest_path: Path | None = None,
    events_file: Path = EVENTS_FILE,
    chunks_path: Path | None = CHUNKS_PATH,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return every DQ flag; `not_yet_released` is severity `info`, not a failure."""
    now = now or datetime.now(timezone.utc)
    today = now.date() if isinstance(now, datetime) else now

    if Path(documents_path).is_file():
        documents = pd.read_parquet(documents_path)
        documents["event_date"] = pd.to_datetime(documents["event_date"]).dt.date
    else:
        documents = pd.DataFrame(columns=[
            "doc_id", "event_date", "doc_type", "sha256", "n_chars",
            "votes_for", "votes_against", "vote_tally", "extractor_version",
        ])
    if "vote_tally" not in documents.columns:
        documents["vote_tally"] = None

    manifest_files: dict[str, Any] = {}
    if manifest_path is not None and Path(manifest_path).is_file():
        manifest_files = json.loads(Path(manifest_path).read_text(encoding="utf-8")).get("files", {})

    chunks_by_doc = None
    if chunks_path is not None and Path(chunks_path).is_file():
        chunks = pd.read_parquet(chunks_path)
        chunks_by_doc = {
            str(doc_id): frame for doc_id, frame in chunks.groupby("doc_id", sort=False)
        }

    flags: list[dict[str, Any]] = []
    scheduled = _scheduled_dates(events_file)
    statements = documents[documents["doc_type"] == "statement"]
    minutes = documents[documents["doc_type"] == "minutes"]
    statement_counts = statements.groupby("event_date").size().to_dict()
    minute_dates = set(minutes["event_date"])

    for event_date in scheduled:
        count = int(statement_counts.get(event_date, 0))
        if count != 1:
            flags.append(_flag(
                "statement_count", event_date=event_date,
                detail=f"{count} statement(s) for scheduled date",
            ))

        minutes_entry = manifest_files.get(f"minutes/{event_date.strftime('%Y%m%d')}.pdf", {})
        status = minutes_entry.get("status")
        if event_date not in minute_dates:
            age = (today - event_date).days
            if status == "not_yet_released":
                flags.append(_flag(
                    "not_yet_released", event_date=event_date,
                    detail=f"minutes not released; manifest status {status}",
                    severity="info",
                ))
            elif age > MINUTES_RELEASE_GRACE_DAYS:
                flags.append(_flag(
                    "minutes_missing", event_date=event_date,
                    detail=f"minutes missing {age} days after the meeting",
                ))

    for row in documents.itertuples(index=False):
        doc_id = str(row.doc_id)
        doc_type = str(row.doc_type)
        event_date = row.event_date
        minimum = STATEMENT_MIN_CHARS if doc_type == "statement" else MINUTES_MIN_CHARS
        if int(row.n_chars) < minimum:
            flags.append(_flag(
                "short_document", doc_id=doc_id, event_date=event_date,
                detail=f"{int(row.n_chars)} characters; minimum {minimum}",
            ))
        if str(row.extractor_version) != EXTRACTOR_VERSION:
            flags.append(_flag(
                "stale_extractor", doc_id=doc_id, event_date=event_date,
                detail=f"stored {row.extractor_version}; current {EXTRACTOR_VERSION}",
            ))
        votes_for = [] if row.votes_for is None else list(row.votes_for)
        votes_against = [] if row.votes_against is None else list(row.votes_against)
        tally = getattr(row, "vote_tally", None)
        if tally is None or (isinstance(tally, float) and pd.isna(tally)):
            tally = None
        if doc_type == "statement" and not votes_for and not tally:
            flags.append(_flag(
                "votes_for_empty", doc_id=doc_id, event_date=event_date,
                detail="statement has neither a votes_for list nor a vote tally",
            ))
        if doc_type == "statement" and tally:
            matched = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", str(tally))
            if matched is None or int(matched.group(2)) != len(votes_against):
                flags.append(_flag(
                    "vote_count_mismatch", doc_id=doc_id, event_date=event_date,
                    detail=f"tally {tally!r} does not match {len(votes_against)} dissent(s)",
                ))

        extension = "html" if doc_type == "statement" else "pdf"
        manifest_entry = manifest_files.get(f"{doc_type}/{event_date.strftime('%Y%m%d')}.{extension}", {})
        stored_sha = manifest_entry.get("sha256")
        if stored_sha is not None and str(stored_sha) != str(row.sha256):
            flags.append(_flag(
                "sha_mismatch", doc_id=doc_id, event_date=event_date,
                detail="stored sha256 differs from manifest; re-extract and re-index",
            ))

        if chunks_by_doc is not None:
            frame = chunks_by_doc.get(doc_id)
            if frame is None or frame.empty or frame["text"].fillna("").str.strip().eq("").all():
                flags.append(_flag(
                    "empty_paragraphs", doc_id=doc_id, event_date=event_date,
                    detail="no non-empty chunks for this document",
                ))

    return flags
