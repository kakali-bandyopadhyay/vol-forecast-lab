"""Build the analysis panel: RV proxies, VIX level and 30-day targets — §3.1–3.2.

Writes data/panel/panel.parquet plus its SHA-256. The panel is git-ignored.

Output discipline (leakage guard). The test period is 2020-01-01 onwards and must not be
inspected before the freeze, so this script prints:

  * training-window statistics only (first session → 2015-12-31): F_min, the VIX tercile
    thresholds and the cross-term mean and share;
  * full-sample coverage counts only: targets dropped, N_t min/max, last complete-target date.

No performance statistic is computed or printed anywhere, and nothing derived from a date after
2015-12-31 is printed. If you add a print, it belongs in one of those two blocks.

Usage:
    python scripts/build_panel.py
    python scripts/build_panel.py --snapshot 20261112T071104Z --out-dir tmp/panel
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import date
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from volab import rv as rv_module  # noqa: E402
from volab import targets as targets_module  # noqa: E402
from volab.data import REPO_ROOT, load_spy, load_vix  # noqa: E402

# Fixed by the pre-registration, not by the data.
WINDOW_START = date(2010, 1, 4)          # §2.2
TRAINING_END = date(2015, 12, 31)        # §5.1 initial training; fixes F_min and the terciles
CALENDAR = "XNYS"
CALENDAR_BUFFER_DAYS = 60                # sessions must extend past the last window end
F_MIN_QUANTILE = 0.01                    # §3.4
TEST_START = date(2020, 1, 1)            # §5.1 test period
TERCILE_QUANTILES = (1.0 / 3.0, 2.0 / 3.0)   # §3.5

# §3.1 robustness proxies (two): close-to-close r_t² and r_on² + Parkinson. Session Garman–Klass
# is built above as the daily `gk` column because §7.3 uses it, but §3.1 does not make it a
# forecast-comparison proxy, so it carries no 30-day target.
ROBUSTNESS_PROXIES = {
    "rv_close_to_close": "close-to-close r_t²",
    "rv_overnight_parkinson": "r_on² + Parkinson",
}


def iso(value) -> str:
    return pd.Timestamp(value).date().isoformat()


def resolve_snapshot(requested: str | None) -> str:
    manifest_path = REPO_ROOT / "data" / "snapshot_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshots = manifest["snapshots"]
    if requested:
        if requested not in snapshots:
            raise SystemExit(f"snapshot {requested} not in {manifest_path.name}")
        return requested
    if len(snapshots) != 1:
        raise SystemExit(f"{len(snapshots)} snapshots present; pass --snapshot explicitly")
    return next(iter(snapshots))


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as saved:
        for chunk in iter(lambda: saved.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_proxies(spy: pd.DataFrame) -> pd.DataFrame:
    """All §3.1 proxies, computed on the loaded frame so the first window row has a prev close."""
    proxies = pd.DataFrame(index=spy.index)
    proxies["r_on"] = rv_module.overnight(spy)
    proxies["r_oc"] = rv_module.session_return(spy)
    proxies["cross_term"] = rv_module.cross_term(spy)
    proxies["gk"] = rv_module.garman_klass(spy)
    proxies["parkinson"] = rv_module.parkinson(spy)
    proxies["rv"] = rv_module.rv_primary(spy)
    proxies["rv_close_to_close"] = rv_module.rv_close_to_close(spy)
    proxies["rv_overnight_parkinson"] = rv_module.rv_overnight_parkinson(spy)
    return proxies


def build_panel(snapshot_id: str) -> tuple[pd.DataFrame, dict]:
    spy = load_spy(snapshot_id)
    vix = load_vix(snapshot_id)

    last_data = pd.Timestamp(spy.index.max())
    first_data = pd.Timestamp(spy.index.min())

    calendar = xcals.get_calendar(CALENDAR)
    session_index = calendar.sessions_in_range(
        first_data, last_data + pd.Timedelta(days=CALENDAR_BUFFER_DAYS)
    )

    proxies = build_proxies(spy)
    panel = pd.concat([spy[["adjOpen", "adjHigh", "adjLow", "adjClose"]], proxies], axis=1)
    panel = panel.loc[pd.Timestamp(WINDOW_START):last_data]

    # VIX level: joined on the observation date, never forward-filled (§2.3).
    panel["vix"] = pd.to_numeric(vix["CLOSE"], errors="coerce").reindex(panel.index)

    # 30-day targets for the primary proxy and each robustness proxy (§3.2).
    for column in ["rv", *ROBUSTNESS_PROXIES]:
        label = "rv_30d" if column == "rv" else f"{column}_30d"
        forward = targets_module.forward_targets(panel[column], session_index)
        panel[label] = forward["rv_30d"]
        if column == "rv":
            panel["n_sessions"] = forward["n_sessions"]

    panel["rv_30d_per_session"] = targets_module.per_session_mean(
        panel[["rv_30d", "n_sessions"]]
    )

    metadata = {
        "snapshot_id": snapshot_id,
        "rows": int(len(panel)),
        "first_session": iso(panel.index[0]),
        "last_data_date": iso(last_data),
        "calendar": f"{CALENDAR}, {len(session_index)} sessions to {iso(session_index[-1])}",
        "buffer_days": CALENDAR_BUFFER_DAYS,
        "columns": list(panel.columns),
    }
    return panel, metadata


def training_statistics(panel: pd.DataFrame) -> dict:
    """§3.4 F_min, §3.5 terciles, §3.1 cross-term — training window only, nothing later."""
    training = panel.loc[: pd.Timestamp(TRAINING_END)]
    with_target = training["rv_30d"].dropna()

    f_min = float(with_target.quantile(F_MIN_QUANTILE))
    vix_training = training["vix"].dropna()
    terciles = [float(vix_training.quantile(q)) for q in TERCILE_QUANTILES]

    cross_mean = float(training["cross_term"].mean())
    rt_squared_mean = float(training["rv_close_to_close"].mean())

    return {
        "rows": int(len(training)),
        "rows_with_target": int(len(with_target)),
        "f_min": f_min,
        "tercile_low": terciles[0],
        "tercile_high": terciles[1],
        "cross_term_mean": cross_mean,
        "cross_term_share": cross_mean / rt_squared_mean,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the RV/VIX/target panel")
    parser.add_argument("--snapshot", default=None, help="snapshot id; defaults to the only one")
    parser.add_argument("--out-dir", default=None, help="defaults to data/panel")
    parser.add_argument("--verbose", action="store_true",
                        help="surface the per-date target-drop warnings (they name 2020+ dates)")
    args = parser.parse_args()

    # volab.targets logs every dropped target at WARNING. That is right for the library, but here
    # it would print one line per dropped date, naming 2020+ dates — outside this script's
    # output contract. The count is reported below; the detail is opt-in.
    logging.getLogger("volab.targets").setLevel(
        logging.WARNING if args.verbose else logging.ERROR
    )

    snapshot_id = resolve_snapshot(args.snapshot)
    out_dir = Path(args.out_dir) if args.out_dir else REPO_ROOT / "data" / "panel"
    out_dir.mkdir(parents=True, exist_ok=True)

    panel, metadata = build_panel(snapshot_id)

    panel_path = out_dir / "panel.parquet"
    panel.to_parquet(panel_path)

    digest = sha256_of(panel_path)
    (out_dir / "panel.sha256").write_text(f"{digest}  panel.parquet\n", encoding="utf-8")
    (out_dir / "panel_manifest.json").write_text(
        json.dumps({**metadata, "sha256": digest}, indent=2) + "\n", encoding="utf-8"
    )

    # --- coverage counts (full sample; no performance statistic) -------------
    dropped = int(panel["rv_30d"].isna().sum())
    complete = panel["rv_30d"].dropna()

    print(f"panel      {metadata['rows']} rows, {metadata['first_session']} .. "
          f"{metadata['last_data_date']}")
    print(f"calendar   {metadata['calendar']}")
    print(f"written    {panel_path}")
    print(f"sha256     {digest}")
    print()
    print(f"targets dropped        {dropped}")
    print(f"N_t min / max          {int(panel['n_sessions'].min())} / {int(panel['n_sessions'].max())}")
    print(f"last complete target   {iso(complete.index[-1])}")
    # Declared exception to the no-2020+ rule: a sample-size count for §5.6, carrying no
    # statistic computed from 2020+ data.
    test_dates = complete.loc[pd.Timestamp(TEST_START):]
    print(f"test-period dates      {len(test_dates)}  "
          f"({iso(test_dates.index[0])} .. {iso(test_dates.index[-1])})")
    print()

    # --- training-window statistics only (§3.4, §3.5, §3.1) ------------------
    stats = training_statistics(panel)
    print(f"training window        {metadata['first_session']} .. {TRAINING_END.isoformat()}")
    print(f"  rows                 {stats['rows']} ({stats['rows_with_target']} with a target)")
    print(f"  F_min (1st pct)      {stats['f_min']:.6e}")
    print(f"  VIX terciles         {stats['tercile_low']:.2f} / {stats['tercile_high']:.2f}")
    print(f"  cross-term mean      {stats['cross_term_mean']:.6e}")
    print(f"  cross-term share     {stats['cross_term_share'] * 100:.1f}% of mean r_t^2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
