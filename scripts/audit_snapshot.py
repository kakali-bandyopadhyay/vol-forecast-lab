"""Step 1 audit: counts that fill PREREGISTRATION §2.4 (and the §2.3 excluded-date list).

Reads one immutable snapshot through volab.data (hash-verified), runs the eight §2.4 checks,
and writes data/audit.json. No network access, no randomness, no result-dependent branching:
the same snapshot must always produce the same JSON.

Usage:
    python scripts/audit_snapshot.py
    python scripts/audit_snapshot.py --snapshot 20261112T071104Z
    python scripts/audit_snapshot.py --out tmp/audit.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd

_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir():                      # normal layout: <repo>/scripts/ + <repo>/src
    sys.path.insert(0, str(_SRC))

from volab.data import REPO_ROOT, load_spy, load_vix, load_vix_fred  # noqa: E402

# --- fixed by the pre-registration, not by the data -------------------------
WINDOW_START = date(2010, 1, 4)     # §2.2 "first session (2010-01-04 or later)"
CALENDAR = "XNYS"
VIX_TOL = 0.01                      # §2.4 |Cboe close - FRED close| <= 0.01
OHLC_EPS = 1e-9                     # float guard; a 1e-9 overshoot is not a violation
DIV_TOL_BP = 10.0                   # §2.4 "adjusted overnight ratio ~ raw gap net of dividend"
EVENT_FILES = {"fomc": "fomc.csv", "cpi": "cpi.csv", "unscheduled": "unscheduled.csv"}
# unscheduled.csv is a provenance record of actions deliberately kept OUT of the event set;
# two of its rows are Sundays with no session, which is correct and must not read as a failure.
EVENT_FILE_ROLE = {"fomc": "event_set", "cpi": "event_set", "unscheduled": "reference_only"}


def iso(ts) -> str:
    return pd.Timestamp(ts).date().isoformat()


def _row_min(a: pd.Series, b: pd.Series) -> pd.Series:
    return a.where(a < b, b)


def _row_max(a: pd.Series, b: pd.Series) -> pd.Series:
    return a.where(a > b, b)


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


def audit_window(spy: pd.DataFrame, cal):
    """Sessions from the pre-registered start to the last usable session in the snapshot."""
    start = cal.date_to_session(pd.Timestamp(WINDOW_START), direction="next")
    last_spy = pd.Timestamp(spy.index.max())
    if not cal.is_session(last_spy):
        raise SystemExit(f"last SPY date {iso(last_spy)} is not an {CALENDAR} session")
    return start, last_spy, cal.sessions_in_range(start, last_spy)


def check_missing_sessions(spy, sessions) -> dict:
    present = set(pd.DatetimeIndex(spy.index))
    session_set = set(sessions)
    missing = [s for s in sessions if s not in present]
    extra = sorted(s for s in present if sessions[0] <= s <= sessions[-1] and s not in session_set)
    return {
        "rule": "every XNYS session in the window is present in the SPY snapshot",
        "sessions_in_window": len(sessions),
        "spy_rows_in_window": int(sum(1 for s in present if sessions[0] <= s <= sessions[-1])),
        "missing_count": len(missing),
        "missing_dates": [iso(s) for s in missing],
        "spy_dates_not_sessions": [iso(s) for s in extra],
    }


def check_ohlc(spy) -> dict:
    o, h, l, c = spy["adjOpen"], spy["adjHigh"], spy["adjLow"], spy["adjClose"]
    bad = (l > _row_min(o, c) + OHLC_EPS) | (h < _row_max(o, c) - OHLC_EPS)
    # the adjustment factor is common to all four fields, so raw must agree exactly
    ro, rh, rl, rc = spy["open"], spy["high"], spy["low"], spy["close"]
    raw_bad = (rl > _row_min(ro, rc) + OHLC_EPS) | (rh < _row_max(ro, rc) - OHLC_EPS)
    return {
        "rule": "adjLow <= min(adjOpen, adjClose) and adjHigh >= max(adjOpen, adjClose)",
        "violations": int(bad.sum()),
        "violation_dates": [iso(i) for i in spy.index[bad]],
        "raw_violations": int(raw_bad.sum()),
        "adjusted_and_raw_agree": bool(bad.equals(raw_bad)),
    }


def check_zero_range(spy) -> dict:
    zero = spy["adjHigh"] == spy["adjLow"]
    flat = zero & (spy["adjOpen"] == spy["adjClose"])
    return {
        "rule": "adjHigh == adjLow; logged and kept, never repaired",
        "count": int(zero.sum()),
        "dates": [iso(i) for i in spy.index[zero]],
        "all_four_equal": int(flat.sum()),
    }


def check_vix_cross_check(vix, fred, sessions) -> dict:
    """Compare on sessions only: a close on a day the exchange was shut is not a price."""
    cboe = pd.to_numeric(vix["CLOSE"], errors="coerce")
    fred_series = pd.to_numeric(fred[fred.columns[0]], errors="coerce")

    in_window = fred_series[(fred_series.index >= sessions[0]) & (fred_series.index <= sessions[-1])]
    fred_blank = int(in_window.isna().sum())          # FRED emits rows for non-observations

    cboe_s = cboe.reindex(sessions)
    fred_s = fred_series.reindex(sessions)
    shared = sessions[cboe_s.notna().to_numpy() & fred_s.notna().to_numpy()]
    diff = (cboe_s.loc[shared] - fred_s.loc[shared]).abs()
    bad = diff > VIX_TOL
    cboe_only = sessions[cboe_s.notna().to_numpy() & fred_s.isna().to_numpy()]

    return {
        "rule": f"|Cboe close - FRED close| <= {VIX_TOL} on shared sessions",
        "tolerance": VIX_TOL,
        "compared_on": "sessions only",
        "shared_sessions": int(len(shared)),
        "mismatches": int(bad.sum()),
        "mismatch_dates": [iso(i) for i in shared[bad.to_numpy()]],
        "max_abs_diff": round(float(diff.max()), 6) if len(diff) else None,
        "fred_blank_rows_in_window": fred_blank,
        "sessions_without_fred_observation": [iso(i) for i in cboe_only],
    }


def check_vix_non_session(vix, fred, spy, sessions) -> dict:
    """VIX (and FRED's copy) must not carry a close on a day the exchange was shut.

    These rows are not forward-fills; they are extra observations in the vendor file. They are
    harmless only if the panel is built on the session index. An outer join would admit them.
    """
    session_set = set(sessions)
    window = vix.index[(vix.index >= sessions[0]) & (vix.index <= sessions[-1])]
    extra = sorted(d for d in window if d not in session_set)

    fred_close = pd.to_numeric(fred[fred.columns[0]], errors="coerce")
    fred_also = [d for d in extra if d in fred_close.index and pd.notna(fred_close.loc[d])]

    repeats = 0
    for d in extra:
        prior = sessions[sessions < d]
        if len(prior) and float(vix.loc[d, "CLOSE"]) == float(vix.loc[prior[-1], "CLOSE"]):
            repeats += 1

    return {
        "rule": "a close on a non-session date is not a price; the panel must be built on the session index",
        "non_session_rows": len(extra),
        "first_date": iso(extra[0]) if extra else None,
        "dates": [iso(d) for d in extra],
        "weekday_counts": {k: int(v) for k, v in
                           pd.Series([pd.Timestamp(d).day_name() for d in extra]).value_counts().items()}
        if extra else {},
        "identical_to_prior_session_close": repeats,
        "fred_carries_same_date": len(fred_also),
        "spy_rows_on_these_dates": sum(1 for d in extra if d in spy.index),
        "note": "extra rows in the vendor file, not a forward-fill; excluded by a session-index join",
    }


def check_ex_dividend(spy, first, last) -> dict:
    """Adjusted overnight ratio must equal the raw gap with the dividend removed.

    The test is fixed in advance: a multiplicative adjustment implies
    adjOpen/prevAdjClose == open/(prevClose - div). The additive form
    (open + div)/prevClose is reported as a diagnostic only, never as the rule.
    """
    prev_close = spy["close"].shift(1)
    prev_adjclose = spy["adjClose"].shift(1)
    div = pd.to_numeric(spy["divCash"], errors="coerce").fillna(0.0)
    div_days = spy.index[(div > 0) & prev_close.notna() & (spy.index >= first) & (spy.index <= last)]

    if int((spy["splitFactor"] != 1).sum()):
        raise SystemExit("snapshot contains split events; the dividend check assumes none")

    adj_ratio = (spy["adjOpen"] / prev_adjclose).loc[div_days]
    form_a = (spy["open"] / (prev_close - div)).loc[div_days]        # open / (prevClose - D)
    form_b = ((spy["open"] + div) / prev_close).loc[div_days]        # (open + D) / prevClose

    dev = (adj_ratio - form_a).abs()      # rule: multiplicative adjustment -> open / (prevClose - D)
    dev_b = (adj_ratio - form_b).abs()    # diagnostic only, not the test
    raw_net = form_a
    convention = "adjOpen/prevAdjClose vs open/(prevClose - div)  [fixed by rule]"

    dev_bp = dev * 1e4
    fails = dev_bp > DIV_TOL_BP
    rows = [
        {
            "date": iso(d),
            "div": round(float(div.loc[d]), 6),
            "adj_ratio": round(float(adj_ratio.loc[d]), 8),
            "raw_net": round(float(raw_net.loc[d]), 8),
            "dev_bp": round(float(dev_bp.loc[d]), 3),
            "pass": bool(not fails.loc[d]),
        }
        for d in div_days
    ]
    return {
        "rule": f"adjusted overnight ratio matches the raw gap net of the dividend within {DIV_TOL_BP} bp",
        "convention": convention,
        "tolerance_bp": DIV_TOL_BP,
        "dividend_days": int(len(div_days)),
        "passes": int((~fails).sum()),
        "fails": int(fails.sum()),
        "max_dev_bp": round(float(dev_bp.max()), 3) if len(dev_bp) else None,
        "max_dev_bp_form_b_diagnostic": round(float(dev_b.max() * 1e4), 3) if len(dev_b) else None,
        "rows": rows,
    }


def check_event_rows(cal, sessions) -> dict:
    out: dict[str, dict] = {}
    session_set = set(sessions)
    for name, file_name in EVENT_FILES.items():
        path = REPO_ROOT / "data" / "events" / file_name
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        date_col = "event_date" if "event_date" in frame.columns else "date"
        url_col = "source_url" if "source_url" in frame.columns else None

        in_sample_col = next((c for c in frame.columns
                              if c.lower() in {"in_event_sample", "in_sample"}), None)
        if in_sample_col is not None:
            mask = frame[in_sample_col].str.strip().str.lower().isin({"true", "1", "yes"})
            sample, outside = frame[mask], frame[~mask]
        else:
            sample, outside = frame, frame.iloc[0:0]

        def to_ts(value):
            try:
                return pd.Timestamp(str(value).strip())
            except (ValueError, TypeError):
                return None

        sample_dates = [to_ts(v) for v in sample[date_col]]
        unparsed = [str(v) for v, ts in zip(sample[date_col], sample_dates) if ts is None]
        non_session = [iso(ts) for ts in sample_dates if ts is not None and ts not in session_set]
        urls = sample[url_col].str.strip() if url_col else pd.Series([""] * len(sample), index=sample.index)
        missing_url = [str(v) for v, u in zip(sample[date_col], urls)
                       if not (u.startswith("http://") or u.startswith("https://"))]

        out[name] = {
            "rule": "every row of the event sample is a session and carries an http(s) source URL",
            "role": EVENT_FILE_ROLE[name],
            "file": f"data/events/{file_name}",
            "rows_in_file": int(len(frame)),
            "rows_in_event_sample": int(len(sample)),
            "in_sample_flag_column": in_sample_col,
            "unparseable_dates": unparsed,
            "non_session_dates": non_session,
            "missing_or_invalid_url": missing_url,
            "all_sessions": not non_session,
            "all_urls_present": not missing_url,
            "rows_outside_event_sample": int(len(outside)),
            "outside_sample_dates": [str(v) for v in outside[date_col]] if len(outside) else [],
        }
    return out


def check_early_closes(cal, sessions) -> dict:
    early = [s for s in cal.early_closes if sessions[0] <= s <= sessions[-1]]
    closes = [cal.session_close(s).tz_convert("America/New_York").strftime("%H:%M") for s in early]
    return {
        "rule": "XNYS sessions whose close is earlier than 16:00 ET are flagged",
        "count": len(early),
        "dates": [iso(s) for s in early],
        "closes_et": closes,
        "distinct_close_times_et": sorted(set(closes)),
    }


def check_excluded_dates(spy, vix, sessions) -> dict:
    spy_dates = set(pd.DatetimeIndex(spy.index))
    vix_dates = set(pd.to_numeric(vix["CLOSE"], errors="coerce").dropna().index)

    miss_spy = [s for s in sessions if s not in spy_dates]
    miss_vix = [s for s in sessions if s not in vix_dates]
    miss_spy_set = set(miss_spy)
    miss_vix_set = set(miss_vix)
    excluded = [s for s in sessions if s in miss_spy_set or s in miss_vix_set]

    return {
        "rule": "a session missing SPY or VIX is excluded from every joint analysis; VIX is never filled",
        "excluded_count": len(excluded),
        "missing_spy": [iso(s) for s in miss_spy],
        "missing_vix": [iso(s) for s in miss_vix],
        "excluded_dates": [iso(s) for s in excluded],
        "usable_sessions": len(sessions) - len(excluded),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 1 audit for vol-forecast-lab")
    parser.add_argument("--snapshot", default=None, help="snapshot id; defaults to the only one present")
    parser.add_argument("--out", default=None, help="output path; defaults to data/audit.json")
    args = parser.parse_args()

    snapshot_id = resolve_snapshot(args.snapshot)
    out_path = Path(args.out) if args.out else REPO_ROOT / "data" / "audit.json"

    spy = load_spy(snapshot_id)
    vix = load_vix(snapshot_id)
    fred = load_vix_fred(snapshot_id)

    cal = xcals.get_calendar(CALENDAR)
    first, last, sessions = audit_window(spy, cal)
    spy_w = spy.loc[first:last]

    checks = {
        "missing_sessions": check_missing_sessions(spy, sessions),
        "ohlc_consistency": check_ohlc(spy_w),
        "zero_range": check_zero_range(spy_w),
        "vix_cboe_vs_fred": check_vix_cross_check(vix, fred, sessions),
        "vix_non_session_rows": check_vix_non_session(vix, fred, spy, sessions),
        "ex_dividend": check_ex_dividend(spy, first, last),
        "event_rows": check_event_rows(cal, sessions),
        "early_closes": check_early_closes(cal, sessions),
        "excluded_dates": check_excluded_dates(spy, vix, sessions),
    }

    payload = {
        "snapshot_id": snapshot_id,
        "calendar": {"name": CALENDAR, "package": "exchange_calendars", "version": xcals.__version__},
        "window": {"first_session": iso(first), "last_session": iso(last), "sessions": len(sessions)},
        "prereg_2_4": {
            "missing_sessions": checks["missing_sessions"]["missing_count"],
            "ohlc_violations": checks["ohlc_consistency"]["violations"],
            "zero_range_days": checks["zero_range"]["count"],
            "vix_mismatches": checks["vix_cboe_vs_fred"]["mismatches"],
            "vix_non_session_rows": checks["vix_non_session_rows"]["non_session_rows"],
            "ex_div_pass": f'{checks["ex_dividend"]["passes"]}/{checks["ex_dividend"]["dividend_days"]}',
            "event_rows": {k: f'{v["rows_in_event_sample"] - len(v["non_session_dates"])}/'
                              f'{v["rows_in_event_sample"]}'
                           for k, v in checks["event_rows"].items() if v["role"] == "event_set"},
            "early_close_sessions": checks["early_closes"]["count"],
            "excluded_dates": checks["excluded_dates"]["excluded_count"],
        },
        "checks": checks,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"run {datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}  snapshot {snapshot_id}  window {iso(first)} -> {iso(last)}  ({len(sessions)} sessions)")
    for key, value in payload["prereg_2_4"].items():
        print(f"  {key:22s} {value}")
    print(f"written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
