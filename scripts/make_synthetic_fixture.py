"""Generate a deterministic, explicitly synthetic market-data snapshot."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path

import exchange_calendars as xcals
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ID = "20240101T000000Z"
SEED = 20261115
SPY_COLUMNS = [
    "date", "open", "high", "low", "close", "adjOpen", "adjHigh", "adjLow",
    "adjClose", "volume", "adjVolume", "divCash", "splitFactor",
]


def sha256(path: Path) -> str:
    """Return a file's SHA-256 digest."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_text(path: Path, content: str) -> None:
    """Write UTF-8 text with deterministic LF newlines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))


def _csv_text(header: list[str], rows: list[list[object]]) -> str:
    """Serialize simple CSV rows with stable number formatting and LF endings."""
    lines = [",".join(header)]
    for row in rows:
        fields = []
        for value in row:
            if isinstance(value, float):
                fields.append(format(value, ".10f").rstrip("0").rstrip(".") or "0")
            else:
                fields.append(str(value))
        lines.append(",".join(fields))
    return "\n".join(lines) + "\n"


def read_script_date(script_name: str, constant_name: str) -> pd.Timestamp:
    """Read a date constant from a repository script without importing its dependencies."""
    script_path = REPO_ROOT / "scripts" / script_name
    tree = ast.parse(script_path.read_text(encoding="utf-8"), filename=str(script_path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == constant_name for target in node.targets
        ):
            value = node.value
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "date":
                return pd.Timestamp(*[ast.literal_eval(argument) for argument in value.args])
            raise ValueError(f"{constant_name} in {script_name} must be a date(...) call")
    raise ValueError(f"{constant_name} was not found in {script_name}")


def generate(output_dir: Path) -> list[Path]:
    """Write fixture files below an output directory and return written paths."""
    output_dir = output_dir.resolve()
    snapshot_dir = output_dir / "snapshots" / SNAPSHOT_ID
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    calendar = xcals.get_calendar("XNYS")
    window_start = read_script_date("audit_snapshot.py", "WINDOW_START")
    first_session = calendar.date_to_session(window_start, direction="next")
    sessions = calendar.sessions_in_range(first_session, pd.Timestamp("2020-12-31"))
    session_count = len(sessions)
    test_start = read_script_date("build_panel.py", "TEST_START")
    test_session = calendar.date_to_session(test_start, direction="next")
    sessions_after_test_start = int((sessions > test_session).sum())
    if sessions_after_test_start < 120:
        raise RuntimeError(
            f"Fixture has only {sessions_after_test_start} XNYS sessions after TEST_START; need at least 120"
        )

    rng = np.random.default_rng(SEED)
    variances = np.empty(session_count, dtype=float)
    returns = np.empty(session_count, dtype=float)
    variance = 1e-4
    for index in range(session_count):
        variances[index] = variance
        returns[index] = np.sqrt(variance) * rng.normal()
        variance = 2e-6 + 0.08 * returns[index] ** 2 + 0.90 * variance

    closes = np.empty(session_count, dtype=float)
    opens = np.empty(session_count, dtype=float)
    highs = np.empty(session_count, dtype=float)
    lows = np.empty(session_count, dtype=float)
    volumes = np.empty(session_count, dtype=np.int64)
    previous_close = 470.0
    for index, log_return in enumerate(returns):
        closes[index] = previous_close * np.exp(log_return)
        opens[index] = previous_close * np.exp(rng.normal(0.0, 0.002))
        spread = abs(rng.normal(0.0, 0.004))
        highs[index] = max(opens[index], closes[index]) * (1.0 + spread)
        lows[index] = min(opens[index], closes[index]) * (1.0 - spread)
        volumes[index] = int(round(70e6 * np.exp(rng.normal(0.0, 0.2))))
        previous_close = closes[index]

    vix_close = np.maximum(
        9.0,
        100.0 * np.sqrt(252.0 * variances) * 1.15 + rng.normal(0.0, 0.5, session_count),
    )
    vix_open = np.empty(session_count, dtype=float)
    vix_high = np.empty(session_count, dtype=float)
    vix_low = np.empty(session_count, dtype=float)
    previous_vix = float(vix_close[0])
    for index, close in enumerate(vix_close):
        vix_open[index] = previous_vix * np.exp(rng.normal(0.0, 0.002))
        spread = abs(rng.normal(0.0, 0.004))
        vix_high[index] = max(vix_open[index], close) * (1.0 + spread)
        vix_low[index] = min(vix_open[index], close) * (1.0 - spread)
        previous_vix = float(close)

    dates = [pd.Timestamp(day).strftime("%Y-%m-%dT00:00:00.000Z") for day in sessions]
    spy_rows = []
    raw_rows = []
    for index, stamp in enumerate(dates):
        row = {
            "date": stamp,
            "open": round(float(opens[index]), 10), "high": round(float(highs[index]), 10),
            "low": round(float(lows[index]), 10), "close": round(float(closes[index]), 10),
            "adjOpen": round(float(opens[index]), 10), "adjHigh": round(float(highs[index]), 10),
            "adjLow": round(float(lows[index]), 10), "adjClose": round(float(closes[index]), 10),
            "volume": int(volumes[index]), "adjVolume": int(volumes[index]),
            "divCash": 0.0, "splitFactor": 1.0,
        }
        raw_rows.append(row)
        spy_rows.append([row[column] for column in SPY_COLUMNS])

    spy_csv = _csv_text(SPY_COLUMNS, spy_rows)
    cboe_rows = []
    fred_rows = []
    for index, day in enumerate(sessions):
        cboe_rows.append([
            pd.Timestamp(day).strftime("%m/%d/%Y"),
            *(float(value) for value in (
                vix_open[index], vix_high[index], vix_low[index], vix_close[index],
            )),
        ])
        fred_rows.append([
            pd.Timestamp(day).strftime("%Y-%m-%d"),
            f"{vix_close[index]:.2f}",
        ])

    outputs = {
        "spy.csv": spy_csv,
        "spy_raw.json": json.dumps(raw_rows, separators=(",", ":"), ensure_ascii=False) + "\n",
        "vix_cboe.csv": "DATE,OPEN,HIGH,LOW,CLOSE\n" + "".join(
            f"{row[0]},{row[1]:.6f},{row[2]:.6f},{row[3]:.6f},{row[4]:.6f}\n"
            for row in cboe_rows
        ),
        "vix_fred.csv": "observation_date,VIXCLS\n" + "".join(
            f"{row[0]},{row[1]}\n" for row in fred_rows
        ),
    }
    for name, content in outputs.items():
        _write_text(snapshot_dir / name, content)

    hashes = {name: sha256(snapshot_dir / name) for name in outputs}
    first_date = pd.Timestamp(sessions[0]).date().isoformat()
    last_date = pd.Timestamp(sessions[-1]).date().isoformat()
    package_versions = {
        "pandas": importlib.metadata.version("pandas"),
        "numpy": importlib.metadata.version("numpy"),
        "python": platform.python_version(),
    }
    sources = {
        source: {
            "url": "synthetic://vol-forecast-lab/20240101T000000Z",
            "row_count": session_count,
            "first_date": first_date,
            "last_date": last_date,
            "files": [{"file_name": filename, "sha256": hashes[filename]} for filename in filenames],
            "status": "available",
            "http_status": None,
            "data_classification": "SYNTHETIC – not market data",
        }
        for source, filenames in {
            "spy": ["spy_raw.json", "spy.csv"],
            "vix_cboe": ["vix_cboe.csv"],
            "vix_fred": ["vix_fred.csv"],
        }.items()
    }
    snapshot_manifest = {
        "snapshot_id": SNAPSHOT_ID,
        "pulled_at_utc": "2024-01-01T00:00:00Z",
        "data_classification": "SYNTHETIC – not market data",
        "sources": sources,
        "package_versions": package_versions,
    }
    _write_text(snapshot_dir / "manifest.json", json.dumps(snapshot_manifest, indent=2) + "\n")
    root_manifest = {
        "data_classification": "SYNTHETIC – not market data",
        "snapshots": {SNAPSHOT_ID: snapshot_manifest},
    }
    _write_text(output_dir / "snapshot_manifest.json", json.dumps(root_manifest, indent=2) + "\n")
    return sorted(output_dir.rglob("*"))


def main() -> int:
    """Generate the repository fixture or a caller-selected fixture directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=REPO_ROOT / "fixtures" / "synthetic" / "data",
        help="fixture data directory (default: fixtures/synthetic/data)",
    )
    args = parser.parse_args()
    generate(args.output_dir)
    print(f"synthetic snapshot written: {args.output_dir.resolve() / 'snapshots' / SNAPSHOT_ID}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
