"""Offline contracts for structured hackathon platform adapters."""

import os
import sys
from datetime import datetime, timezone


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_mlh_schema_cards_preserve_event_urls_and_dates():
    from tools.hackathon_adapters.mlh import MLHAdapter

    html = """
    <section>
      <a itemScope="" itemType="https://schema.org/Event"
         href="https://alpha.example/?utm_source=mlh">
        <meta itemProp="url" content="https://alpha.example/"/>
        <meta itemProp="eventAttendanceMode"
              content="https://schema.org/OnlineEventAttendanceMode"/>
        <meta itemProp="startDate" content="2027-02-01T10:00:00Z"/>
        <meta itemProp="endDate" content="2027-02-03T18:00:00Z"/>
        <h4>Alpha Hackathon</h4>
        <span itemProp="name">Online</span>
      </a>
      <a itemScope="" itemType="https://schema.org/Event"
         href="https://beta.example/">
        <meta itemProp="url" content="https://beta.example/"/>
        <meta itemProp="startDate" content="2027-03-01T10:00:00Z"/>
        <meta itemProp="endDate" content="2027-03-02T18:00:00Z"/>
        <h4>Beta Hackathon</h4>
      </a>
    </section>
    """

    adapter = MLHAdapter()
    raw_events = adapter.parse_listing(html, "https://mlh.io/seasons/2027/events")
    candidates = [adapter.normalize(event) for event in raw_events]

    assert len(candidates) == 2
    assert candidates[0].source_url == "https://alpha.example/"
    assert candidates[0].event_start == "2027-02-01T10:00:00Z"
    assert candidates[0].event_end == "2027-02-03T18:00:00Z"
    assert candidates[0].mode == "online"
    assert candidates[1].source_url == "https://beta.example/"


def test_mlh_fallback_date_uses_explicit_season_year():
    from tools.hackathon_adapters.mlh import MLHAdapter

    start, end = MLHAdapter()._parse_mlh_date("AUG 22 - 24", year=2027)

    assert start.startswith("2027-08-22")
    assert end.startswith("2027-08-24")


def test_mlh_completed_events_do_not_consume_discovery_limit():
    from tools.hackathon_adapters.base import HackathonCandidate
    from tools.hackathon_adapters.mlh import MLHAdapter

    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    completed = HackathonCandidate(
        title="Completed Hackathon",
        source_name="MLH",
        source_url="https://completed.example",
        event_start="2026-05-01T00:00:00Z",
        event_end="2026-05-02T00:00:00Z",
    )
    upcoming = HackathonCandidate(
        title="Upcoming Hackathon",
        source_name="MLH",
        source_url="https://upcoming.example",
        event_start="2026-08-01T00:00:00Z",
        event_end="2026-08-02T00:00:00Z",
    )

    assert MLHAdapter._is_current_or_future(completed, now) is False
    assert MLHAdapter._is_current_or_future(upcoming, now) is True


def test_general_search_queries_are_loaded_from_config_for_both_years(monkeypatch):
    from tools.hackathon_adapters import general_search

    general_search._QUERIES_CACHE = None
    queries = general_search._load_search_queries()

    assert "黑客松 报名 2026" in queries
    assert "黑客松 报名 2027" in queries
    assert "site:dorahacks.io/hackathon 2027" in queries
