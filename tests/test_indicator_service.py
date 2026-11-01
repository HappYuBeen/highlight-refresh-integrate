"""extract_indicators 로직 테스트 · 가짜 DB 조회·본문·LLM 사용 (장유빈)"""
import pytest

from hri.domain.schemas import Indicator
from hri.service import indicator_service
from hri.service.indicator_service import (
	INDICATOR_ALIASES,
	INDICATOR_SYSTEM_PROMPT,
	IndicatorList,
	extract_indicators,
	normalize_indicator_name,
)

def _ind(name="경제성장률", value=2.0, unit="%", period="2027", source="text", needs_review=False, page=None) -> Indicator:
	return Indicator(name=name, value=value, unit=unit, period=period, source=source, needs_review=needs_review, page=page)


def _use(monkeypatch, saved=None, text="본문", result=None):
	"""DB 조회·본문·LLM 호출을 가짜로 바꾸고, 무엇이 불렸는지 기록해 돌려준다."""
	calls = {"select": [], "text": [], "llm": [], "insert": []}

	def fake_select(report_id):
		calls["select"].append(report_id)
		return saved or []

	def fake_text(report_id):
		calls["text"].append(report_id)
		return text

	def fake_llm(body):
		calls["llm"].append(body)
		return result

	def fake_insert(report_id, indicators):
		calls["insert"].append((report_id, indicators))
		return len(indicators)

	monkeypatch.setattr(indicator_service, "select_indicators", fake_select)
	monkeypatch.setattr(indicator_service, "get_report_text", fake_text)
	monkeypatch.setattr(indicator_service, "_request_indicators", fake_llm)
	monkeypatch.setattr(indicator_service, "insert_indicators", fake_insert)
	return calls


def test_saved_indicators_returned_without_llm(monkeypatch):
	saved = [_ind()]
	calls = _use(monkeypatch, saved=saved)
	assert extract_indicators(7) == saved
	assert calls["select"] == [7]
	assert calls["text"] == [] and calls["llm"] == [] and calls["insert"] == []


def test_empty_text_returns_empty_without_llm(monkeypatch):
	calls = _use(monkeypatch, text="  \n")
	assert extract_indicators(7) == []
	assert calls["llm"] == [] and calls["insert"] == []


def test_extract_sets_report_id_and_saves(monkeypatch):
	calls = _use(monkeypatch, text="본문 전체", result=IndicatorList(indicators=[_ind(), _ind("실업률", 3.1, "%", "2027")]))
	indicators = extract_indicators(7)
	assert [i.name for i in indicators] == ["경제성장률", "실업률"]
	assert all(i.report_id == 7 for i in indicators)
	assert calls["text"] == [7]
	assert calls["llm"] == ["본문 전체"]  # get_report_text 결과가 그대로 LLM에 간다
	assert calls["insert"] == [(7, indicators)]


def test_source_and_needs_review_are_kept(monkeypatch):
	chart = _ind("원/달러 환율", 1390.0, "원", "2027Q1", source="chart", needs_review=True)
	_use(monkeypatch, result=IndicatorList(indicators=[chart]))
	[item] = extract_indicators(7)
	assert item.source == "chart" and item.needs_review is True


def test_invalid_period_is_dropped(monkeypatch):
	bad = [_ind(period="2027년"), _ind(period="2026-Q2"), _ind(period="202613"), _ind(period="")]
	good = _ind(period="202607")
	_use(monkeypatch, result=IndicatorList(indicators=[*bad, good]))
	assert [i.period for i in extract_indicators(7)] == ["202607"]


@pytest.mark.parametrize("period", ["2027", "2026Q4", "202612", "20261031"])
def test_valid_periods_are_kept(monkeypatch, period):
	_use(monkeypatch, result=IndicatorList(indicators=[_ind(period=period)]))
	assert [i.period for i in extract_indicators(7)] == [period]


def test_lowercase_quarter_is_normalized(monkeypatch):
	_use(monkeypatch, result=IndicatorList(indicators=[_ind(period=" 2026q2 ")]))
	assert [i.period for i in extract_indicators(7)] == ["2026Q2"]


def test_duplicates_are_removed_keeping_first(monkeypatch):
	items = [
		_ind(source="text"),
		_ind(source="table"),  # 이름·시점·값·단위가 같아서 중복
		_ind(value=2.1),  # 값이 다르면 다른 지표
		_ind(period="2028"),  # 시점이 다르면 다른 지표
	]
	_use(monkeypatch, result=IndicatorList(indicators=items))
	result = extract_indicators(7)
	assert [(i.value, i.period, i.source) for i in result] == [(2.0, "2027", "text"), (2.1, "2027", "text"), (2.0, "2028", "text")]


def test_names_are_unified_by_alias(monkeypatch):
	items = [_ind("GDP 성장률", 2.0), _ind("CPI", 2.3), _ind("신용카드 승인액", 5.0)]
	_use(monkeypatch, result=IndicatorList(indicators=items))
	assert [i.name for i in extract_indicators(7)] == ["경제성장률", "소비자물가 상승률", "신용카드 승인액"]


def test_unreadable_llm_result_is_not_saved(monkeypatch):
	calls = _use(monkeypatch, result=None)
	assert extract_indicators(7) == []
	assert calls["insert"] == []


def test_no_indicators_found_returns_empty(monkeypatch):
	_use(monkeypatch, result=IndicatorList(indicators=[]))
	assert extract_indicators(7) == []


@pytest.mark.parametrize(
	"raw, expected",
	[
		("경제성장률", "경제성장률"),
		("민간소비", "민간소비 증가율"),
		("설비투자 증가율", "설비투자 증가율"),
		("KOSPI", "코스피"),
		("국고채(3년) 금리", "국고채 3년 금리"),
		("원·달러 환율", "원/달러 환율"),
		("GDP 대비 국가채무 비율", "국가채무 비율"),
		("근원 CPI 상승률", "근원물가 상승률"),
		("  GDP 성장률  ", "경제성장률"),
		("실질GDP성장률", "경제성장률"),  # 공백이 없어도 같은 이름
		("cpi", "소비자물가 상승률"),  # 대소문자 무시
		("소비자물가상승률", "소비자물가 상승률"),
		("원달러 환율", "원/달러 환율"),
		("신용카드 승인액", "신용카드 승인액"),  # 통일표에 없으면 그대로
	],
)
def test_normalize_indicator_name(raw, expected):
	assert normalize_indicator_name(raw) == expected


def test_prompt_mentions_key_rules():
	assert "(추정)" in INDICATOR_SYSTEM_PROMPT
	assert "needs_review" in INDICATOR_SYSTEM_PROMPT
	assert "YYYYQn" in INDICATOR_SYSTEM_PROMPT
	assert "page" in  INDICATOR_SYSTEM_PROMPT

# ---------------- 이름 통일표 ----------------
def test_every_alias_maps_to_its_canonical_name():
	for canonical, aliases in INDICATOR_ALIASES.items():
		for alias in [canonical, *aliases]:
			assert normalize_indicator_name(alias) == canonical, alias


def test_aliases_are_not_shared_between_indicators():
	seen: dict[str, str] = {}
	for canonical, aliases in INDICATOR_ALIASES.items():
		for alias in [canonical, *aliases]:
			key = indicator_service._name_key(alias)
			assert seen.setdefault(key, canonical) == canonical, f"{alias!r}가 {seen[key]!r}와 {canonical!r}에 모두 있습니다"

# ---------------- 출처 쪽 ----------------
def test_page_is_kept_when_cleaning(monkeypatch):
	_use(monkeypatch, result=IndicatorList(indicators=[_ind(page=12), _ind("실업률", 3.1, page=None)]))
	assert [i.page for i in extract_indicators(7)] == [12, None]


@pytest.mark.parametrize("page", [0, -3])
def test_invalid_page_becomes_none(monkeypatch, page):
	_use(monkeypatch, result=IndicatorList(indicators=[_ind(page=page)]))
	assert [i.page for i in extract_indicators(7)] == [None]


def test_page_does_not_split_duplicates(monkeypatch):
	_use(monkeypatch, result=IndicatorList(indicators=[_ind(page=3), _ind(page=9)]))
	assert [i.page for i in extract_indicators(7)] == [3]  # 이름·시점·값·단위가 같으면 먼저 나온 것만 남는다