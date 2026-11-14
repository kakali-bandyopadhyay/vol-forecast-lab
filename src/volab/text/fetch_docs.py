"""Fetch the pre-registered FOMC statements and minutes into a snapshot."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

REPO_ROOT = Path(__file__).resolve().parents[3]
EVENTS_FILE = REPO_ROOT / "data" / "events" / "fomc.csv"
USER_AGENT = "vol-forecast-lab research prototype"
REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 3
REQUEST_INTERVAL_SECONDS = 1.0
STATEMENT_URL = "https://www.federalreserve.gov/newsevents/pressreleases/monetary{date}a.htm"
MINUTES_URL = "https://www.federalreserve.gov/monetarypolicy/files/fomcminutes{date}.pdf"
# Entries with these statuses are never re-requested; "error" entries are retried on the next run.
FINAL_STATUSES = frozenset({"ok", "not_found"})
LOGGER = logging.getLogger(__name__)


def build_document_url(event_date: str, doc_type: str) -> str:
    """Build the Federal Reserve URL for a YYYYMMDD event date and document type."""
    if len(event_date) != 8 or not event_date.isdigit():
        raise ValueError("event_date must use YYYYMMDD")
    datetime.strptime(event_date, "%Y%m%d")
    if doc_type == "statement":
        return STATEMENT_URL.format(date=event_date)
    if doc_type == "minutes":
        return MINUTES_URL.format(date=event_date)
    raise ValueError("doc_type must be statement or minutes")


def _sha256(data: bytes) -> str:
    """Return the SHA-256 digest of downloaded bytes."""
    return hashlib.sha256(data).hexdigest()


def _read_event_dates(events_file: Path) -> list[str]:
    """Read unique, valid event dates from the FOMC calendar CSV."""
    event_dates: list[str] = []
    seen: set[str] = set()
    with events_file.open("r", encoding="utf-8-sig", newline="") as csv_file:
        for row in csv.DictReader(csv_file):
            raw_date = (row.get("event_date") or "").strip()
            parsed = datetime.strptime(raw_date, "%Y-%m-%d")
            event_date = parsed.strftime("%Y%m%d")
            if event_date not in seen:
                event_dates.append(event_date)
                seen.add(event_date)
    return event_dates


def fetch_documents(
    snapshot_id: str,
    *,
    limit: int | None = None,
    doc_type: str = "all",
    root: Path = REPO_ROOT,
    events_file: Path | None = None,
    get: Callable[..., Any] = requests.get,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """Fetch missing files for a snapshot and persist an incremental manifest."""
    try:
        parsed_id = datetime.strptime(snapshot_id, "%Y%m%dT%H%M%SZ")
    except ValueError as exc:
        raise ValueError("snapshot_id must use YYYYMMDDTHHMMSSZ") from exc
    if parsed_id.strftime("%Y%m%dT%H%M%SZ") != snapshot_id:
        raise ValueError("snapshot_id must use YYYYMMDDTHHMMSSZ")
    if doc_type not in {"statement", "minutes", "all"}:
        raise ValueError("doc_type must be statement, minutes, or all")
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")

    snapshot_root = root / "data" / "snapshots" / snapshot_id
    if not snapshot_root.is_dir():
        raise FileNotFoundError(f"Snapshot directory does not exist: {snapshot_id}")
    snapshot_dir = snapshot_root / "docs"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = snapshot_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("snapshot_id") != snapshot_id:
            raise ValueError("document manifest snapshot id mismatch")
        manifest.setdefault("files", {})
    else:
        manifest = {"snapshot_id": snapshot_id, "files": {}}

    dates = _read_event_dates(events_file or root / "data" / "events" / "fomc.csv")
    if limit is not None:
        dates = dates[:limit]
    kinds = ("statement", "minutes") if doc_type == "all" else (doc_type,)
    last_request_at: float | None = None

    for event_date in dates:
        for kind in kinds:
            extension = "html" if kind == "statement" else "pdf"
            relative_name = f"{kind}/{event_date}.{extension}"
            destination = snapshot_dir / relative_name
            previous = manifest["files"].get(relative_name)
            if previous is not None and previous.get("status") in FINAL_STATUSES:
                LOGGER.info("Skipping manifest entry %s", relative_name)
                continue
            if destination.exists():
                manifest["files"][relative_name] = {
                    "url": build_document_url(event_date, kind),
                    "http_status": None,
                    "bytes": None,
                    "sha256": None,
                    "fetched_at_utc": now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "status": "error",
                    "error": "destination already exists outside the manifest",
                }
                _write_manifest(manifest_path, manifest)
                LOGGER.warning("Refusing to overwrite unregistered file %s", relative_name)
                continue

            url = build_document_url(event_date, kind)
            http_status: int | None = None
            payload: bytes | None = None
            error: str | None = None
            for attempt in range(1, MAX_RETRIES + 2):
                if last_request_at is not None:
                    delay = REQUEST_INTERVAL_SECONDS - (monotonic() - last_request_at)
                    if delay > 0:
                        sleep(delay)
                last_request_at = monotonic()
                try:
                    response = get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS)
                    http_status = int(response.status_code)
                    if http_status == 200:
                        payload = bytes(response.content)
                        error = None
                        break
                    error = f"HTTP {http_status}"
                    # 429 (rate limited) and 5xx are retried; other 4xx are permanent.
                    if http_status != 429 and 400 <= http_status < 500:
                        break
                except requests.RequestException as exc:
                    error = f"{type(exc).__name__}: request failed"
                LOGGER.warning("Document request failed for %s (attempt %d/%d)", relative_name, attempt, MAX_RETRIES + 1)

            fetched_at = now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            if payload is not None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as saved_file:
                    saved_file.write(payload)
                entry = {
                    "url": url,
                    "http_status": http_status,
                    "bytes": len(payload),
                    "sha256": _sha256(payload),
                    "fetched_at_utc": fetched_at,
                    "status": "ok",
                }
            else:
                entry = {
                    "url": url,
                    "http_status": http_status,
                    "bytes": 0,
                    "sha256": None,
                    "fetched_at_utc": fetched_at,
                    "status": "not_found" if http_status == 404 else "error",
                    "error": error or "request failed",
                }
            manifest["files"][relative_name] = entry
            _write_manifest(manifest_path, manifest)
    return manifest


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Persist the incrementally updated document manifest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for incremental snapshot document downloads."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, help="existing snapshot id, formatted YYYYMMDDTHHMMSSZ")
    parser.add_argument("--limit", type=int, help="limit the number of event dates processed")
    parser.add_argument("--doc-type", choices=("statement", "minutes", "all"), default="all")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        fetch_documents(args.snapshot, limit=args.limit, doc_type=args.doc_type)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        LOGGER.error("Document fetch failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
