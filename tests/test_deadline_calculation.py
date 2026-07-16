from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.tools.event_schema import calculate_days_remaining, is_deadline_expired


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("deadline", "expected"),
    [
        ("2026-07-16T23:59:59+00:00", 0),
        ("2026-07-23T12:00:00+00:00", 7),
        ("2026-08-15T12:00:00+00:00", 30),
        ("2026-07-16T11:59:59+00:00", -1),
        (None, None),
        ("not-a-date", None),
    ],
)
def test_calculate_days_remaining(deadline, expected):
    assert calculate_days_remaining(deadline, now=NOW) == expected


def test_deadline_timezone_controls_calendar_day_boundary():
    now = datetime(2026, 7, 16, 23, 30, tzinfo=timezone.utc)
    deadline = "2026-07-17T10:00:00+08:00"

    assert calculate_days_remaining(deadline, now=now) == 0


def test_expired_requires_a_valid_past_deadline():
    assert is_deadline_expired("2026-07-16T11:59:59+00:00", now=NOW)
    assert not is_deadline_expired("2026-07-16T23:59:59+00:00", now=NOW)
    assert not is_deadline_expired(None, now=NOW)


def test_naive_reference_time_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        calculate_days_remaining(
            "2026-07-17T12:00:00+00:00",
            now=datetime(2026, 7, 16, 12, 0),
        )


def test_query_and_recommendation_share_deadline_helper():
    query_source = (PROJECT_ROOT / "src/tools/event_query_tool.py").read_text(
        encoding="utf-8"
    )
    profile_source = (PROJECT_ROOT / "src/tools/user_profile.py").read_text(
        encoding="utf-8"
    )

    assert 'calculate_days_remaining(event.get("signup_deadline"))' in query_source
    assert 'is_deadline_expired(event.get("signup_deadline"))' in profile_source
    assert 'event["days_remaining"] = calculate_days_remaining' in profile_source
