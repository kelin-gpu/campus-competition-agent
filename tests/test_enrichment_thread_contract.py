from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_enrichment_uses_client_timeout_not_process_signals():
    source = (PROJECT_ROOT / "src/tools/event_enrichment.py").read_text(encoding="utf-8")

    assert "import signal" not in source
    assert "SIGALRM" not in source
    assert "timeout=timeout" in source
    assert "llm_timeout=per_record_timeout" in source


def test_wechat_enrichment_has_rule_based_fallback():
    source = (PROJECT_ROOT / "src/tools/wechat_data_source.py").read_text(encoding="utf-8")

    assert "except Exception as e:" in source
    assert "_rule_based_fallback(raw_event, match_ministry_contest(title))" in source
    assert "enriched.append(result)" in source
