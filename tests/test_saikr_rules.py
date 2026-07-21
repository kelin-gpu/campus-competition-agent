"""Contracts for Saikr source filtering and alias de-duplication."""

import os
import sys
import importlib.util
from pathlib import Path


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_promotion_title_is_filtered_but_contest_is_retained():
    from tools.saikr_rules import is_likely_saikr_promotion

    assert is_likely_saikr_promotion("本科生保送研究生（保研）定位分析") is True
    assert is_likely_saikr_promotion("第七届华数杯大学生数学建模竞赛") is False


def test_title_identity_collapses_platform_seo_suffix():
    from tools.saikr_rules import saikr_title_identity

    plain = "2026年第七届华数杯大学生数学建模竞赛"
    seo = plain + "-大学生竞赛-赛氪竞赛网-全国大学生比赛信息网"

    assert saikr_title_identity(plain) == saikr_title_identity(seo)


def test_standalone_listing_parser_filters_promotions_and_alias_titles():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "crawl_saikr_hot_contests.py"
    spec = importlib.util.spec_from_file_location("crawl_saikr_hot_contests", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    page = """
    <html><body>
      <a href="/vse/math-model">第七届华数杯大学生数学建模竞赛</a>
      <a href="/vse/math-model-alias">第七届华数杯大学生数学建模竞赛-大学生竞赛-赛氪竞赛网</a>
      <a href="/vse/graduate-plan">本科生保送研究生（保研）定位分析</a>
    </body></html>
    """

    parsed = module.parse_list_page(page, "https://www.saikr.com/index/hot/contest")
    merged = module.merge_records([parsed], 50)

    assert len(merged) == 1
    assert merged[0]["detail_url"] == "https://www.saikr.com/vse/math-model"
