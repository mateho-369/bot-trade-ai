"""Isolated offline diagnostic and identifier listing never access news network or broker."""

import json

from core.settings import Settings
from scripts import inspect_news_sources
from scripts.smoke_news import run


async def test_offline_news_diagnostic_no_orders_network_native_or_stage_evidence(tmp_path):
    result = await run(tmp_path)
    assert result["synthetic"] and result["scripted_http_transport_only"]
    assert result["actual_http_network_calls"] == result["real_orders"] == result["simulated_orders"] == 0
    assert not result["native_sdk_imported"] and not result["eligible_stage_evidence"]
    assert all(result["checks"].values()) and "NOT entitled feed validation" in result["warning"]


def test_inspection_only_lists_nonsecret_ids_and_scopes(capsys, monkeypatch):
    cfg = Settings(_env_file=None, news_api_key="FAKE_SECRET_NEVER_PRINT", newsapi_enabled=True)
    monkeypatch.setattr(inspect_news_sources, "get_settings", lambda: cfg)
    assert inspect_news_sources.main() == 0
    output = capsys.readouterr().out
    data = json.loads(output)
    assert data["sources"] == [{"source_id": "newsapi", "coverage": None}]
    assert "FAKE_SECRET_NEVER_PRINT" not in output and "zero HTTP" in data["warning"]
