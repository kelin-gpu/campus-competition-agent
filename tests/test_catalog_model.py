"""Tests for the new catalog/edition data model."""

import pytest
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from storage.database.db import get_engine
from storage.database.catalog_models import (
    CompetitionCatalog,
    EventEdition,
    FieldEvidence,
)
from tools.catalog_service import merge_event, merge_catalog, get_or_create_catalog
from tools.data_sync_workflow import _determine_status, _is_expired, _is_likely_ad_or_training
from sqlalchemy.orm import sessionmaker


SessionLocal = sessionmaker(bind=get_engine())


class TestStatusSemantics:
    """P0: status must not default to '报名中' for missing deadline."""

    def test_no_deadline_status_is_unknown(self):
        assert _determine_status(None) == "暂无本届信息"

    def test_empty_deadline_status_is_unknown(self):
        assert _determine_status("") == "暂无本届信息"

    def test_future_deadline_status_is_open(self):
        future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        assert _determine_status(future) == "报名中"

    def test_imminent_deadline_status(self):
        future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        assert _determine_status(future) == "即将截止"

    def test_expired_deadline_status(self):
        past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        assert _determine_status(past) == "已截止"


class TestAdFiltering:
    """P0: ads/trainings must be filtered out."""

    def test_training_is_filtered(self):
        assert _is_likely_ad_or_training("蓝桥杯 Python 培训班招生") is True

    def test_planning_is_filtered(self):
        assert _is_likely_ad_or_training("2026 保研规划讲座") is True

    def test_genuine_contest_is_kept(self):
        assert _is_likely_ad_or_training("第十四届全国大学生数学竞赛报名通知") is False


class TestCatalogService:
    """P0: catalog + edition separation."""

    def test_ministry_catalog_only(self):
        """Ministry directory entries should create catalog, not edition."""
        session = SessionLocal()
        try:
            catalog = merge_catalog(session, {
                "title": "全国大学生数学建模竞赛",
                "source_name": "教育部竞赛目录",
                "scope_type": "校外竞赛",
            })
            session.refresh(catalog)
            assert catalog.is_ministry_approved is True
            assert catalog.normalized_title is not None
        finally:
            session.close()

    def test_merge_event_creates_edition(self):
        """Saikr/school events should create both catalog and edition."""
        session = SessionLocal()
        try:
            future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
            edition = merge_event(session, {
                "event_id": f"TEST-{uuid4().hex[:8]}",
                "title": "2027年第十五届全国大学生数学竞赛",
                "source_name": "赛氪",
                "source_url": "https://example.com/contest",
                "scope_type": "校外竞赛",
                "category": "数学",
                "signup_deadline": future,
                "target_major": "全校各专业",
                "target_grade": "大一,大二,大三",
                "contest_level": "国家级",
                "status": "报名中",
            })
            session.refresh(edition)
            assert edition.edition_year == 2027
            assert edition.catalog_id is not None
            assert edition.status == "报名中"
            catalog = session.get(CompetitionCatalog, edition.catalog_id)
            assert catalog is not None
            assert "全国大学生数学竞赛" in catalog.normalized_title
        finally:
            session.close()

    def test_field_evidence_is_recorded(self):
        """Evidence records must be created for key fields."""
        session = SessionLocal()
        try:
            future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
            event_id = f"TEST-{uuid4().hex[:8]}"
            edition = merge_event(session, {
                "event_id": event_id,
                "title": "2027年测试竞赛",
                "source_name": "赛氪",
                "source_url": "https://example.com/test",
                "scope_type": "校外竞赛",
                "signup_deadline": future,
                "status": "报名中",
            })
            session.commit()
            evidence = session.query(FieldEvidence).filter(
                FieldEvidence.edition_id == edition.edition_id
            ).all()
            fields = {e.field_name for e in evidence}
            assert "signup_deadline" in fields
            assert "source_url" in fields
        finally:
            session.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
