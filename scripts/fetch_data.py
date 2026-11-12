"""Fetch one immutable snapshot of SPY, Cboe VIX, and FRED VIXCLS data."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import logging
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
SPY_URL = "https://api.tiingo.com/tiingo/daily/SPY/prices"
CBOE_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS"
TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 3
SPY_FIELDS = [
    "date", "open", "high", "low", "close", "adjOpen", "adjHigh", "adjLow",
    "adjClose", "volume", "adjVolume", "divCash", "splitFactor",
]
LOGGER = logging.getLogger("fetch_data")


def sha256_file(path: Path) -> str:
    """Return a file's lowercase SHA-256 digest."""
    digest = hashlib.sha256()
    with path.open("rb") as saved_file:
        for chunk in iter(lambda: saved_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _request_bytes(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    get: Callable[..., Any] = requests.get,
) -> tuple[bytes | None, int | None, str | None]:
    """Download bytes with a bounded retry count and a timeout on every attempt."""
    last_status: int | None = None
    last_error: str | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = get(url, headers=headers, params=params, timeout=TIMEOUT_SECONDS)
            last_status = response.status_code
            if not 200 <= response.status_code < 300:
                last_error = f"HTTP {response.status_code}"
            else:
                return response.content, response.status_code, None
        except requests.RequestException as exc:
            # Exception text can include request details; only retain the exception type.
            last_error = f"{type(exc).__name__}: request failed"
        LOGGER.warning("Source request failed (attempt %d/%d): %s", attempt, MAX_ATTEMPTS, last_error)
    return None, last_status, last_error or "request failed"


def _source_record(url: str) -> dict[str, Any]:
    """Create an unavailable-by-default source record."""
    return {
        "url": url,
        "row_count": None,
        "first_date": None,
        "last_date": None,
        "files": [],
        "status": "unavailable",
    }


def _file_record(directory: Path, name: str) -> dict[str, str]:
    """Describe a file that has already been written to the snapshot."""
    path = directory / name
    return {"file_name": name, "sha256": sha256_file(path)}


def _fetch_spy(directory: Path, api_key: str, get: Callable[..., Any]) -> dict[str, Any]:
    """Fetch Tiingo EOD rows and persist raw JSON plus the requested-column CSV."""
    record = _source_record(SPY_URL)
    params = {"startDate": "2009-12-01", "endDate": datetime.now(timezone.utc).date().isoformat()}
    raw, status, error = _request_bytes(
        SPY_URL,
        headers={"Authorization": f"Token {api_key}"},
        params=params,
        get=get,
    )
    if raw is None:
        record.update({"http_status": status, "error": error})
        return record
    try:
        rows = json.loads(raw.decode("utf-8"))
        if not isinstance(rows, list):
            raise ValueError("expected a JSON array")
        frame = pd.DataFrame(rows)
        missing = [field for field in SPY_FIELDS if field not in frame.columns]
        if missing and len(frame):
            raise ValueError("response is missing expected price fields")
        frame = frame.reindex(columns=SPY_FIELDS)
        csv_text = frame.to_csv(index=False, lineterminator="\n")
        (directory / "spy_raw.json").write_bytes(raw)
        (directory / "spy.csv").write_text(csv_text, encoding="utf-8", newline="")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        record.update({"http_status": status, "error": f"invalid Tiingo response: {type(exc).__name__}"})
        return record
    dates = [str(row.get("date", ""))[:10] for row in rows if row.get("date")]
    record.update({
        "status": "available",
        "http_status": status,
        "row_count": len(rows),
        "first_date": min(dates) if dates else None,
        "last_date": max(dates) if dates else None,
        "files": [_file_record(directory, "spy_raw.json"), _file_record(directory, "spy.csv")],
    })
    return record


def _csv_metadata(raw: bytes, source: str) -> tuple[int, str | None, str | None]:
    """Count non-header CSV records and infer date bounds without changing saved bytes."""
    text = raw.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return 0, None, None
    header = [column.strip().lower() for column in rows[0]]
    if source == "cboe":
        date_index = next((i for i, name in enumerate(header) if name in {"date", "observation_date"}), 0)
    else:
        date_index = 0
    dates = [row[date_index].strip() for row in rows[1:] if len(row) > date_index and row[date_index].strip()]
    normalized = []
    for value in dates:
        if value[:4].isdigit():
            normalized.append(value[:10])
        elif len(value) >= 10 and value[2] == "/" and value[5] == "/":
            # Cboe uses MM/DD/YYYY; store ISO so min/max compare correctly.
            normalized.append(f"{value[6:10]}-{value[0:2]}-{value[3:5]}")
    return len(rows) - 1, (min(normalized) if normalized else None), (max(normalized) if normalized else None)


def _fetch_csv_source(
    directory: Path,
    *,
    name: str,
    url: str,
    source: str,
    get: Callable[..., Any],
) -> dict[str, Any]:
    """Fetch and persist a CSV source byte-for-byte, recording useful row metadata."""
    record = _source_record(url)
    raw, status, error = _request_bytes(url, get=get)
    if raw is None:
        record.update({"http_status": status, "error": error})
        return record
    try:
        count, first_date, last_date = _csv_metadata(raw, source)
    except (UnicodeDecodeError, csv.Error) as exc:
        record.update({"http_status": status, "error": f"invalid CSV response: {type(exc).__name__}"})
        return record
    (directory / name).write_bytes(raw)
    record.update({
        "status": "available",
        "http_status": status,
        "row_count": count,
        "first_date": first_date,
        "last_date": last_date,
        "files": [_file_record(directory, name)],
    })
    return record


def _versions() -> dict[str, str]:
    """Return runtime versions recorded with each snapshot."""
    return {"pandas": pd.__version__, "requests": requests.__version__, "python": platform.python_version()}


def pull_snapshot(
    *,
    root: Path = REPO_ROOT,
    get: Callable[..., Any] = requests.get,
    now: datetime | None = None,
) -> str:
    """Create one immutable snapshot and add its summary to the repository manifest."""
    load_dotenv(root / ".env")
    api_key = __import__("os").environ.get("TIINGO_API_KEY", "")
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    snapshot_id = timestamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = root / "data" / "snapshots" / snapshot_id
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.mkdir(exist_ok=False)
    pulled_at = timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    sources = {
        "spy": _fetch_spy(directory, api_key, get) if api_key else _source_record(SPY_URL),
        "vix_cboe": _fetch_csv_source(directory, name="vix_cboe.csv", url=CBOE_URL, source="cboe", get=get),
        "vix_fred": _fetch_csv_source(directory, name="vix_fred.csv", url=FRED_URL, source="fred", get=get),
    }
    if not api_key:
        sources["spy"]["error"] = "TIINGO_API_KEY is not configured"
        LOGGER.warning("SPY source unavailable: TIINGO_API_KEY is not configured")

    snapshot = {"snapshot_id": snapshot_id, "pulled_at_utc": pulled_at, "sources": sources, "package_versions": _versions()}
    (directory / "manifest.json").write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")

    registry_path = root / "data" / "snapshot_manifest.json"
    registry: dict[str, Any] = {"snapshots": {}}
    if registry_path.exists():
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry.setdefault("snapshots", {})
    registry["snapshots"][snapshot_id] = snapshot
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    LOGGER.info("Snapshot %s created; source availability recorded in manifest", snapshot_id)
    return snapshot_id


def verify_snapshot(snapshot_id: str, *, root: Path = REPO_ROOT) -> bool:
    """Recompute every saved-file digest and report whether the snapshot matches."""
    manifest_path = root / "data" / "snapshots" / snapshot_id / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("snapshot_id") != snapshot_id:
            raise ValueError("snapshot id mismatch")
        for source in manifest["sources"].values():
            for saved_file in source.get("files", []):
                path = manifest_path.parent / saved_file["file_name"]
                if not path.is_file() or sha256_file(path) != saved_file["sha256"]:
                    raise ValueError(f"hash mismatch: {saved_file['file_name']}")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        LOGGER.error("Snapshot verification failed: %s", exc)
        return False
    LOGGER.info("Snapshot %s verified", snapshot_id)
    return True


def main(argv: list[str] | None = None, *, root: Path = REPO_ROOT) -> int:
    """Run a pull or verify operation from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", metavar="SNAPSHOT_ID", help="verify all saved-file hashes")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stdout)
    if args.verify:
        return 0 if verify_snapshot(args.verify, root=root) else 1
    try:
        pull_snapshot(root=root)
    except FileExistsError:
        LOGGER.error("Snapshot directory already exists; refusing to overwrite")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
