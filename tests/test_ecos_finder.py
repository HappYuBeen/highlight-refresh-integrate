"""ECOS 활용 데이터 판단(ecos_finder.find_usable_data) 테스트 · 가짜 ECOS 클라이언트·가짜 LLM으로 확인 (류현수)

네트워크·API 키 없이 돌아간다. 캐시 폴더는 tmp_path로 바꿔 끼운다.
"""

import sys
import types

import pytest
from ecos.exceptions import EcosResponseError
from ecos.models import StatisticItem, StatisticTable

from hri.api import ecos_finder
from hri.domain.schemas.external import Indicator
from hri.exception.errors import ExternalApiError


def _table(code, name, cycle):
	return StatisticTable(stat_code=code, stat_name=name, grp_code="", grp_name="", org_name="한국은행", cycle=cycle)


def _item(code, name, cycle, group="Group1", start="2000Q1", end="2026Q2", stat_code="200Y102"):
	return StatisticItem(
		stat_code=stat_code, stat_name="", grp_code=group, grp_name=group, item_code=code, item_name=name,
		cycle=cycle, start_time=start, end_time=end, data_cnt="100",
	)


TABLES = [
	_table("", "2. 국민계정", ""),  # 분류 제목 줄 (주기 없음)
	_table("200Y102", "2.1.1.2. 주요지표(분기지표) 국내총생산", "Q"),
	_table("200Y101", "2.1.1.1. 주요지표(연간지표) 국내총생산", "A"),
	_table("901Y027", "8.3.1. 경제활동인구 실업률", "M"),
	_table("722Y001", "1.3.1. 한국은행 기준금리", "D"),
]
ITEMS = {
	"200Y102": [
		_item("10111", "국내총생산(실질, 계절조정, 전기비)", "Q"),
		_item("10211", "국민총소득(실질)", "Q"),
	],
	"901Y027": [
		_item("I61BC", "실업률", "M", start="199906", end="202608", stat_code="901Y027"),
		_item("I28A", "원계열", "M", group="Group2", start="199906", end="202608", stat_code="901Y027"),
		_item("I28B", "계절조정", "M", group="Group2", start="199906", end="202608", stat_code="901Y027"),
	],
}


class FakeEcos:
	"""list_tables·list_items 흉내. 받은 요청을 calls에 기록하고, fail에 넣은 이름은 예외를 낸다."""

	def __init__(self, tables=TABLES, items=ITEMS, fail=()):
		self.tables, self.items, self.fail = list(tables), items, set(fail)
		self.calls = []

	def _page(self, rows, start_num, end_num):
		page = rows[start_num - 1:end_num]
		if not page:
			raise EcosResponseError("INFO-200", "해당하는 데이터가 없습니다.")
		return page

	def list_tables(self, stat_code="", *, start_num=1, end_num=100):
		self.calls.append(("tables", start_num, end_num))
		if "tables" in self.fail:
			raise RuntimeError("ECOS 장애")
		return self._page(self.tables, start_num, end_num)

	def list_items(self, stat_code, *, start_num=1, end_num=100):
		self.calls.append(("items", stat_code))
		if stat_code in self.fail:
			raise RuntimeError("항목 조회 실패")
		return self._page(self.items.get(stat_code, []), start_num, end_num)


@pytest.fixture(autouse=True)
def tmp_cache(monkeypatch, tmp_path):
	"""캐시 폴더를 테스트마다 새 임시 폴더로 바꾼다."""
	monkeypatch.setattr(ecos_finder, "CACHE_DIR", tmp_path)
	return tmp_path


@pytest.fixture
def fake_llm(monkeypatch):
	"""진짜 llm_client 대신 끼우는 가짜. answers에 넣은 응답(예외면 raise)을 차례로 돌려주고, 요청은 calls에 (schema, messages)로 기록"""
	answers, calls = [], []

	class Model:
		def with_structured_output(self, schema):
			self.schema = schema
			return self

		def invoke(self, messages):
			calls.append((self.schema, messages))
			assert answers, "가짜 LLM 응답을 다 썼습니다 (예상보다 LLM을 더 불렀습니다)"
			answer = answers.pop(0)
			if isinstance(answer, Exception):
				raise answer
			return answer

	module = types.ModuleType("hri.llm.llm_client")
	module.get_chat_model = lambda **kwargs: Model()
	monkeypatch.setitem(sys.modules, "hri.llm.llm_client", module)
	return answers, calls


def _keywords(**by_name):
	return ecos_finder._KeywordList(items=[ecos_finder._KeywordSet(name=n, keywords=k) for n, k in by_name.items()])


def _ind(name, period, value=0.6, unit="%"):
	return Indicator(name=name, value=value, unit=unit, period=period)


GDP_CHOICE = ecos_finder._EcosPick(usable=True, indicator_no=1, stat_code="200Y102", item_code="10111", reason="실질 GDP 전기비 성장률")
UNEMP_CHOICE = ecos_finder._EcosPick(usable=True, indicator_no=2, stat_code="901Y027", item_code="I61BC", item_code2="I28B", reason="계절조정 실업률")


# [확인 1] 모든 지표의 후보를 한 번에 보여 주고, 고른 짝 하나만 EcosQuery로 돌려준다
def test_returns_single_best_query(fake_llm):
	answers, calls = fake_llm
	indicators = [_ind("경제성장률", "2026Q1"), _ind("실업률", "202607", 2.8), _ind("경제성장률", "2026q2")]
	answers.extend([_keywords(경제성장률=["국내총생산", "GDP"], 실업률=["실업률", "경제활동인구"]), UNEMP_CHOICE])
	query = ecos_finder.find_usable_data(indicators, FakeEcos())

	assert (query.stat_code, query.item_code, query.item_code2, query.cycle) == ("901Y027", "I61BC", "I28B", "M")
	assert (query.start, query.end, query.indicator_name, query.reason) == ("202607", "202607", "실업률", "계절조정 실업률")
	assert len(calls) == 2  # 검색어 1번 + 판별 1번 (지표가 여러 개여도 판별은 한 번)
	prompt = calls[1][1][-1]["content"]
	# 지표는 이름·단위·주기로 묶여 번호가 붙고, 지표마다 주기가 맞는 후보만 들어간다
	assert "[지표 1] 경제성장률 (단위 %, 주기 Q) 값: 2026Q1 0.6, 2026Q2 0.6" in prompt
	assert "[지표 2] 실업률" in prompt
	assert "200Y102" in prompt and "901Y027" in prompt
	assert "200Y101" not in prompt and "722Y001" not in prompt


# [확인 2] 같은 지표의 시점이 여러 개면 수록 기간 안의 가장 최근 시점으로 조회한다 (전망치 2027Q4는 제외)
def test_uses_latest_period_in_range(fake_llm):
	answers, _ = fake_llm
	answers.extend([_keywords(경제성장률=["국내총생산"]), GDP_CHOICE])
	indicators = [_ind("경제성장률", "2026Q1"), _ind("경제성장률", "2027Q4"), _ind("경제성장률", "2026Q2")]
	query = ecos_finder.find_usable_data(indicators, FakeEcos())
	assert (query.start, query.end, query.indicator_name) == ("2026Q2", "2026Q2", "경제성장률")


# [확인 3] 없는 지표 번호, 그 지표의 후보에 없는 코드(LLM이 지어낸 코드), usable=false는 None
@pytest.mark.parametrize("choice", [
	GDP_CHOICE.model_copy(update={"indicator_no": 9}),
	GDP_CHOICE.model_copy(update={"stat_code": "999Y999"}),
	GDP_CHOICE.model_copy(update={"item_code": "00000"}),
	GDP_CHOICE.model_copy(update={"stat_code": "901Y027", "item_code": "I61BC"}),  # 다른 지표(실업률)의 후보
	UNEMP_CHOICE.model_copy(update={"item_code": "I28A"}),  # 두 번째 단계 항목을 첫 항목코드로 고름
	ecos_finder._EcosPick(usable=False, reason="단위가 맞는 항목이 없음"),
	RuntimeError("LLM 오류"),
])
def test_rejects_invalid_choice(fake_llm, choice):
	answers, _ = fake_llm
	answers.extend([_keywords(경제성장률=["국내총생산"], 실업률=["실업률"]), choice])
	indicators = [_ind("경제성장률", "2026Q1"), _ind("실업률", "202607", 2.8)]
	assert ecos_finder.find_usable_data(indicators, FakeEcos()) is None


# [확인 4] 같은 표에 없는 item_code2는 빼고 조회한다 (하위 항목 전체 조회)
def test_drops_unknown_item_code2(fake_llm):
	answers, _ = fake_llm
	answers.extend([_keywords(경제성장률=["국내총생산"]), GDP_CHOICE.model_copy(update={"item_code2": "ZZZ"})])
	query = ecos_finder.find_usable_data([_ind("경제성장률", "2026Q1")], FakeEcos())
	assert query.item_code2 is None


# [확인 5] 시점이 모두 수록 기간 밖이면(전망치만) 후보가 없어 판별 LLM을 부르지 않는다
def test_only_forecasts(fake_llm):
	answers, calls = fake_llm
	answers.append(_keywords(경제성장률=["국내총생산"]))
	assert ecos_finder.find_usable_data([_ind("경제성장률", "2027Q4")], FakeEcos()) is None
	assert len(calls) == 1


# [확인 6] 기준 시점 형식이 틀린 지표만 있으면 ECOS·LLM을 부르지 않는다
def test_invalid_period_skips_everything(fake_llm):
	_, calls = fake_llm
	client = FakeEcos()
	assert ecos_finder.find_usable_data([_ind("경제성장률", "2026년 2분기")], client) is None
	assert calls == [] and client.calls == []


# [확인 7] 후보 통계표가 없는 지표는 판별 프롬프트에서 빠진다
def test_drops_indicator_without_candidates(fake_llm):
	answers, calls = fake_llm
	answers.extend([_keywords(가계부채비율=["가계신용"], 경제성장률=["국내총생산"]), GDP_CHOICE])
	query = ecos_finder.find_usable_data([_ind("가계부채비율", "2026Q1"), _ind("경제성장률", "2026Q1")], FakeEcos())
	assert query.indicator_name == "경제성장률"
	assert "가계부채비율" not in calls[1][1][-1]["content"]


# [확인 8] 검색어 LLM이 실패해도 지표 이름으로 검색해 계속한다
def test_keyword_failure_falls_back_to_name(fake_llm):
	answers, _ = fake_llm
	answers.extend([RuntimeError("LLM 오류"), ecos_finder._EcosPick(usable=True, indicator_no=1, stat_code="901Y027", item_code="I61BC", reason="실업률")])
	query = ecos_finder.find_usable_data([_ind("실업률", "202607", 2.8)], FakeEcos())
	assert query.stat_code == "901Y027"


# [확인 9] 항목 목록 조회가 실패한 표는 건너뛰고, 통계표 목록 조회 실패는 ExternalApiError
def test_ecos_errors(fake_llm, tmp_cache):
	answers, _ = fake_llm
	answers.append(_keywords(경제성장률=["국내총생산"]))
	assert ecos_finder.find_usable_data([_ind("경제성장률", "2026Q1")], FakeEcos(fail={"200Y102"})) is None

	(tmp_cache / "statistic_tables.json").unlink()  # 캐시가 있으면 목록을 다시 부르지 않으므로 지운다
	with pytest.raises(ExternalApiError, match="ECOS 장애"):
		ecos_finder.find_usable_data([_ind("경제성장률", "2026Q1")], FakeEcos(fail={"tables"}))


# [확인 10] 통계표 목록: 여러 쪽을 모두 받고, 분류 제목 줄은 빼고, 캐시가 있으면 다시 부르지 않는다
def test_table_list_paging_and_cache(monkeypatch, tmp_cache):
	monkeypatch.setattr(ecos_finder, "PAGE_SIZE", 2)
	client = FakeEcos()
	tables = ecos_finder.load_table_list(client)
	assert [t.stat_code for t in tables] == ["200Y102", "200Y101", "901Y027", "722Y001"]
	assert [c for c in client.calls if c[0] == "tables"] == [("tables", 1, 2), ("tables", 3, 4), ("tables", 5, 6)]
	assert (tmp_cache / "statistic_tables.json").exists()

	again = FakeEcos()
	assert ecos_finder.load_table_list(again) == tables
	assert again.calls == []
	assert ecos_finder.load_table_list(again, refresh=True) == tables
	assert again.calls


# [확인 11] 오래된 캐시는 쓰지 않는다
def test_expired_cache(monkeypatch):
	ecos_finder.load_item_list("200Y102", FakeEcos())
	monkeypatch.setattr(ecos_finder.time, "time", lambda: 10**12)
	client = FakeEcos()
	ecos_finder.load_item_list("200Y102", client)
	assert client.calls == [("items", "200Y102")]


# [확인 12] 기준 시점 → ECOS 주기
@pytest.mark.parametrize(("period", "cycle"), [("2025", "A"), ("2026Q2", "Q"), ("2026q2", "Q"), ("202607", "M"), ("20261023", "D"), ("2026-07", None)])
def test_period_to_cycle(period, cycle):
	assert ecos_finder.period_to_cycle(period) == cycle
