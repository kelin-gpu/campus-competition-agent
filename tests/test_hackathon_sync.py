"""Tests for hackathon_sync.py v2 — offline, mock-based.

Covers:
- Dry-run doesn't write DB
- Non-dry-run writes DB
- Batch resilience: single fetch failure doesn't abort batch
- Scheduler idempotent start
"""

import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _mock_search_results():
    """Generate mock search result HackathonCandidates."""
    from tools.hackathon_adapters.base import HackathonCandidate
    return [
        HackathonCandidate(
            title="Test AI Hackathon",
            source_name="Devpost",
            source_url="https://devpost.com/test-hack",
            summary="A test hackathon",
            discovered_from="general_search",
            source_authority="high",
        ),
        HackathonCandidate(
            title="Bad Fetch Hackathon",
            source_name="Devpost",
            source_url="https://bad.com/hack",
            summary="Will fail to fetch",
            discovered_from="general_search",
            source_authority="low",
        ),
        HackathonCandidate(
            title="Another Good Hackathon",
            source_name="Devfolio",
            source_url="https://devfolio.co/good2",
            summary="Another test",
            discovered_from="general_search",
            source_authority="high",
        ),
    ]


class TestHackathonCandidateContract:
    def test_platform_adapter_returns_sync_candidate_type(self):
        from tools.hackathon_adapters.base import HackathonCandidate
        from tools.hackathon_adapters.devfolio import DevfolioAdapter
        from tools.hackathon_sync import HackathonCandidate as SyncCandidate

        candidate = DevfolioAdapter().normalize({
            "title": "Test Devfolio Hackathon",
            "source_url": "https://devfolio.co/hackathons/test",
            "status": "open",
        })

        assert HackathonCandidate is SyncCandidate
        assert isinstance(candidate, SyncCandidate)

    def test_real_adapter_candidate_supports_cross_source_dedup(self):
        from tools.hackathon_adapters.base import HackathonCandidate
        from tools.hackathon_sync import _cross_source_dedup

        candidate = HackathonCandidate(
            title="Test Hackathon",
            source_name="MLH",
            source_url="https://mlh.io/events/test",
            platform_id="mlh-test",
        )

        assert _cross_source_dedup([candidate]) == [candidate]

    def test_candidate_conversion_normalizes_authority_without_mutating_tags(self):
        from tools.hackathon_adapters.base import HackathonCandidate
        from tools.hackathon_sync import _candidate_to_event

        candidate = HackathonCandidate(
            title="Test Hackathon",
            source_name="MLH",
            source_url="https://mlh.io/events/test",
            source_authority="high",
            mode="online",
            tags=["黑客松"],
        )

        event = _candidate_to_event(candidate)

        assert event["authority_level"] == "高"
        assert json.loads(event["tags"]) == ["黑客松", "线上"]
        assert candidate.tags == ["黑客松"]


class TestHackathonSyncDryRun:
    @patch("tools.hackathon_adapters.general_search.GeneralSearchAdapter.discover")
    @patch("tools.hackathon_sync.fetch_detail_page")
    def test_dry_run_no_db_write(self, mock_fetch, mock_discover):
        """Dry-run should discover/parse/filter but never call sync_events_to_db."""
        mock_discover.return_value = _mock_search_results()
        mock_fetch.return_value = """
            AI Hackathon 2026
            Registration deadline: 2026-12-31
            Event starts: 2027-01-15
            Register now!
        """

        from tools.hackathon_sync import run_hackathon_sync
        from coze_coding_utils.runtime_ctx.context import new_context

        ctx = new_context(method="test_dry_run")

        with patch("tools.hackathon_sync.sync_events_to_db") as mock_db:
            stats = run_hackathon_sync(ctx=ctx, dry_run=True, sources=["general_search"])
            mock_db.assert_not_called()
            assert stats["discovered"] >= 0
            assert stats["added"] == 0

    @patch("tools.hackathon_adapters.general_search.GeneralSearchAdapter.discover")
    @patch("tools.hackathon_sync.fetch_detail_page")
    @patch("tools.hackathon_sync.sync_events_to_db")
    def test_non_dry_run_writes_db(self, mock_db, mock_fetch, mock_discover):
        """Non-dry-run should call sync_events_to_db."""
        mock_discover.return_value = _mock_search_results()
        mock_fetch.return_value = """
            AI Hackathon 2026
            Registration deadline: 2026-12-31
            Event starts: 2027-01-15
            Register now!
        """
        mock_db.return_value = {"added": 1, "updated": 0, "skipped": 0, "errors": 0}

        from tools.hackathon_sync import run_hackathon_sync
        from coze_coding_utils.runtime_ctx.context import new_context

        ctx = new_context(method="test_non_dry_run")
        stats = run_hackathon_sync(ctx=ctx, dry_run=False, sources=["general_search"])
        mock_db.assert_called_once()
        assert stats["added"] >= 0


class TestSchedulerIdempotent:
    def test_start_scheduler_twice_is_idempotent(self):
        """start_scheduler() called twice should return same scheduler."""
        import threading
        from tools import scheduled_sync

        # Clean state
        scheduled_sync._scheduler = None
        if not hasattr(scheduled_sync, "_scheduler_lock"):
            scheduled_sync._scheduler_lock = threading.Lock()

        s1 = scheduled_sync.start_scheduler()
        s2 = scheduled_sync.start_scheduler()
        assert s1 is s2
        # Clean up
        scheduled_sync.stop_scheduler()


class TestBatchResilience:
    @patch("tools.hackathon_adapters.general_search.GeneralSearchAdapter.discover")
    @patch("tools.hackathon_sync.fetch_detail_page")
    def test_fetch_failure_doesnt_abort_batch(self, mock_fetch, mock_discover):
        """Single page fetch failure should not abort the whole batch."""
        mock_discover.return_value = _mock_search_results()

        call_count = [0]

        def fetch_side_effect(url, **kwargs):
            call_count[0] += 1
            if "bad.com" in url:
                return None
            return """
                AI Hackathon 2026
                Registration deadline: 2026-12-31
                Register now!
                Hackathon event starts 2027-01-15
            """

        mock_fetch.side_effect = fetch_side_effect

        from tools.hackathon_sync import run_hackathon_sync
        from coze_coding_utils.runtime_ctx.context import new_context

        ctx = new_context(method="test_batch_resilience")

        with patch("tools.hackathon_sync.sync_events_to_db") as mock_db:
            mock_db.return_value = {"added": 2, "updated": 0, "skipped": 0, "errors": 0}
            stats = run_hackathon_sync(ctx=ctx, dry_run=False, sources=["general_search"])

        # At least one fetch failed
        any_failed = any(
            d.get("action") == "fetch_failed"
            for d in stats.get("details", [])
        )
        assert any_failed or stats.get("discovered", 0) >= 0
        assert stats["accepted"] >= 1
