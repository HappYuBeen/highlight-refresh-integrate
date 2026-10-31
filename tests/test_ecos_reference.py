"""ECOS 참고 섹션 테스트 · build_ecos_reference, on_compare (장유빈)"""
import sys
import types

import pytest

from hri.domain.schemas import EcosQuery, Indicator
from hri.service import indicator_service
from hri.view import indicator_view


def _ind(rid, name="경제성장률", value=2.1, period="2025", unit="%"):
	return Indicator(report_id=rid, name=name, value=value, unit=unit, period=period)


def _use_ecos(monkeypatch, query, values):
	"""find_usable_data·fetch_indicators를 가짜로 바꿔 끼운다 (import는 함수 안에서 하므로 sys.modules에 넣는다)."""
	finder = types.ModuleType("hri.api.ecos_finder")
	finder.find_usable_data = lambda indicators: query
	client = types.ModuleType("hri.api.ecos_client")
	client.fetch_indicators = lambda q, name: values
	monkeypatch.setitem(sys.modules, "hri.api.ecos_finder", finder)
	monkeypatch.setitem(sys.modules, "hri.api.ecos_client", client)


@pytest.fixture(autouse=True)
def fake_reports(monkeypatch):
	data = {1: [_ind(1, value=2.1), _ind(1, name="실업률", value=3.0)], 2: [_ind(2, value=1.9)]}
	monkeypatch.setattr(indicator_service, "extract_indicators", lambda rid: data.get(rid, []))
	monkeypatch.setattr(indicator_service, "_report_titles", lambda ids: {1: "보고서A", 2: "보고서B"})


def test_reference_shows_report_and_ecos_values(monkeypatch):
	query = EcosQuery(stat_code="200Y102", cycle="A", item_code="10111", start="2025", end="2025",
		reason="실질 GDP 성장률", indicator_name="경제성장률")
	_use_ecos(monkeypatch, query, [_ind(0, value=2.0)])

	text = indicator_service.build_ecos_reference([1, 2])

	assert indicator_service.ECOS_TITLE in text
	assert "보고서A" in text and "보고서B" in text      # 같은 이름의 보고서 값
	assert "ECOS 200Y102" in text and "| 2 |" in text   # ECOS 값
	assert "실업률" not in text                          # 고른 지표만 보여 준다


def test_reference_empty_without_query_or_values(monkeypatch):
	_use_ecos(monkeypatch, None, [])
	assert indicator_service.build_ecos_reference([1, 2]) == ""

	query = EcosQuery(stat_code="200Y102", cycle="A", item_code="10111", start="2025", end="2025", indicator_name="경제성장률")
	_use_ecos(monkeypatch, query, [])
	assert indicator_service.build_ecos_reference([1, 2]) == ""


def test_on_compare_returns_ecos_section_separately(monkeypatch):
	monkeypatch.setattr(indicator_view, "build_compare_table", lambda ids: "표")
	monkeypatch.setattr(indicator_view, "plot_indicators", lambda ids: "chart.png")
	monkeypatch.setattr(indicator_view, "build_ecos_reference", lambda ids: "ECOS 섹션")

	table, ecos, image, _ = indicator_view.on_compare([1, 2], progress=lambda *a, **k: None)

	assert table == "표" and ecos == "ECOS 섹션" and image == "chart.png"


def test_on_compare_keeps_table_when_ecos_fails(monkeypatch):
	def broken(ids):
		raise RuntimeError("ECOS 오류")

	monkeypatch.setattr(indicator_view, "build_compare_table", lambda ids: "표")
	monkeypatch.setattr(indicator_view, "plot_indicators", lambda ids: "chart.png")
	monkeypatch.setattr(indicator_view, "build_ecos_reference", broken)

	table, ecos, image, _ = indicator_view.on_compare([1, 2], progress=lambda *a, **k: None)

	assert table == "표" and ecos == "" and image == "chart.png"