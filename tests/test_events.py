"""Offline tests for the event calendars (Step 2), run against data/events/.

The calendars are pre-registered inputs, so these tests pin both their shape and the exact
numbers §2.4 and §7 depend on. A change to any of these constants is a pre-registration
change, not a test fix.
"""

from __future__ import annotations

import csv
import re
import warnings
from pathlib import Path

import exchange_calendars as xcals
from pandas.errors import Pandas4Warning
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EVENTS_DIR = REPO_ROOT / "data" / "events"

CALENDAR = "XNYS"
CPI_RELEASE_TIME = "08:30"
SESSION_OPEN_MINUTES = 9 * 60 + 30
SESSION_CLOSE_MINUTES = 16 * 60

# PREREGISTRATION §7.1 / §2.4 — changing these requires a DEVIATIONS entry.
EXPECTED_ROWS = {"fomc": 134, "cpi": 202}
EXPECTED_IN_SAMPLE = {"fomc": 134, "cpi": 200}

# The two CPI releases that fell on Good Friday; excluded by rule, listed for transparency.
GOOD_FRIDAY = {"2017-04-14", "2020-04-10"}

# §7.1: days carrying both indicators.
DUAL_DAYS = {
    "2013-10-30", "2014-09-17", "2014-12-17", "2016-03-16", "2017-03-15",
    "2017-06-14", "2017-12-13", "2019-12-11", "2020-06-10", "2024-06-12",
}

# §7.1: emergency control dates, by release date, Sunday actions mapped to the next session.
EMERGENCY_CONTROL = ["2010-05-10", "2019-10-11", "2020-03-03", "2020-03-16", "2020-03-23"]

# §7 removes nothing else; the only reference month BLS never published.
MISSING_REFERENCE_MONTH = "2025-10"


def _read(name: str) -> list[dict[str, str]]:
    path = EVENTS_DIR / f"{name}.csv"
    with path.open(encoding="utf-8", newline="") as saved:
        return list(csv.DictReader(saved))


@pytest.fixture(scope="module")
def calendar():
    # exchange_calendars currently reads timezone-aware schedule Series.values while
    # constructing its session cache; silence only that upstream compatibility warning.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Series.values returning an ndarray that drops timezone information",
            category=Pandas4Warning,
        )
        return xcals.get_calendar(CALENDAR)


@pytest.fixture(scope="module")
def fomc() -> list[dict[str, str]]:
    return _read("fomc")


@pytest.fixture(scope="module")
def cpi() -> list[dict[str, str]]:
    return _read("cpi")


@pytest.fixture(scope="module")
def unscheduled() -> list[dict[str, str]]:
    return _read("unscheduled")


def _in_sample(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in rows if row["in_event_sample"].strip().lower() == "true"]


def _time_minutes(value: str) -> int:
    """Minutes past midnight from a leading HH:MM, ignoring any trailing note."""
    match = re.match(r"^\s*(\d{1,2}):(\d{2})", value)
    assert match is not None, f"unparseable time: {value!r}"
    return int(match.group(1)) * 60 + int(match.group(2))


# --- shape ------------------------------------------------------------------


def test_row_counts_match_prereg(fomc, cpi):
    assert len(fomc) == EXPECTED_ROWS["fomc"]
    assert len(cpi) == EXPECTED_ROWS["cpi"]


def test_in_event_sample_is_boolean(fomc, cpi):
    for rows in (fomc, cpi):
        assert {row["in_event_sample"].strip().lower() for row in rows} <= {"true", "false"}


def test_in_sample_counts_match_prereg(fomc, cpi):
    assert len(_in_sample(fomc)) == EXPECTED_IN_SAMPLE["fomc"]
    assert len(_in_sample(cpi)) == EXPECTED_IN_SAMPLE["cpi"]


def test_every_row_has_a_source_url(fomc, cpi, unscheduled):
    for name, rows in (("fomc", fomc), ("cpi", cpi), ("unscheduled", unscheduled)):
        for row in rows:
            url = row["source_url"].strip()
            assert url.startswith(("http://", "https://")), f"{name}: missing URL on {row}"


def test_no_duplicate_event_dates(fomc, cpi):
    for name, rows in (("fomc", fomc), ("cpi", cpi)):
        dates = [row["event_date"] for row in rows]
        assert len(dates) == len(set(dates)), f"{name}: duplicate dates"


# --- sessions ---------------------------------------------------------------


def test_event_sample_rows_are_sessions(calendar, fomc, cpi):
    for name, rows in (("fomc", fomc), ("cpi", cpi)):
        for row in _in_sample(rows):
            stamp = row["event_date"].strip()
            assert calendar.is_session(stamp), f"{name}: {stamp} is not an {CALENDAR} session"


def test_excluded_rows_are_exactly_the_good_friday_releases(calendar, cpi):
    excluded = {row["event_date"] for row in cpi
                if row["in_event_sample"].strip().lower() == "false"}
    assert excluded == GOOD_FRIDAY
    for stamp in excluded:
        assert not calendar.is_session(stamp), f"{stamp} unexpectedly a session"


def test_effective_session_is_a_session(calendar, cpi):
    for row in cpi:
        stamp = row["effective_session"].strip()
        assert calendar.is_session(stamp), f"effective_session {stamp} is not a session"


def test_effective_session_equals_event_date_except_good_friday(cpi):
    differing = {row["event_date"] for row in cpi
                 if row["event_date"].strip() != row["effective_session"].strip()}
    assert differing == GOOD_FRIDAY


def test_emergency_control_dates_are_sessions_and_not_scheduled_events(calendar, fomc, cpi):
    scheduled = {row["event_date"].strip() for row in _in_sample(fomc)}
    scheduled |= {row["event_date"].strip() for row in _in_sample(cpi)}
    for stamp in EMERGENCY_CONTROL:
        assert calendar.is_session(stamp), f"emergency control {stamp} is not a session"
        assert stamp not in scheduled, f"emergency control {stamp} collides with a scheduled event"


# --- release times ----------------------------------------------------------


def test_cpi_rows_release_at_0830(cpi):
    for row in cpi:
        assert row["time_et"].strip() == CPI_RELEASE_TIME, f"CPI {row['event_date']}: {row['time_et']}"


def test_fomc_rows_fall_inside_the_regular_session(fomc):
    for row in fomc:
        minutes = _time_minutes(row["time_et"])
        assert SESSION_OPEN_MINUTES < minutes < SESSION_CLOSE_MINUTES, (
            f"FOMC {row['event_date']}: {row['time_et']} outside 09:30-16:00 ET"
        )


# --- overlap and reference months -------------------------------------------


def test_same_day_cpi_and_fomc_are_listed(fomc, cpi):
    fomc_days = {row["event_date"].strip() for row in _in_sample(fomc)}
    cpi_days = {row["event_date"].strip() for row in _in_sample(cpi)}
    assert fomc_days & cpi_days == DUAL_DAYS


def test_reference_months_are_consecutive_except_the_unpublished_one(cpi):
    months = sorted({row["reference_month"].strip() for row in cpi})
    first_year, first_month = (int(part) for part in months[0].split("-"))
    last_year, last_month = (int(part) for part in months[-1].split("-"))

    expected, year, month = [], first_year, first_month
    while (year, month) <= (last_year, last_month):
        expected.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    assert set(expected) - set(months) == {MISSING_REFERENCE_MONTH}


# --- provenance regressions -------------------------------------------------


def test_unscheduled_dates_all_parse(unscheduled):
    """Guards the '2011-2013' range that once sat in the date column as an unparseable value."""
    for row in unscheduled:
        stamp = row["date"].strip()
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", stamp), f"non-date value in date column: {stamp!r}"
