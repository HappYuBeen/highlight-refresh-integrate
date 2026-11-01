"""Tool Calling 도구 목록 테스트 · TOOLS (WBS 3.7.7)

도구 6개가 명세대로 만들어졌는지 확인한다 (네트워크·DB·LLM·.env 없이 실행).
- report_ids는 LLM이 아니라 그래프 상태에서 주입되고(InjectedState), 도구 스키마(LLM이 보는 인자)에는 보이지 않는다.
- 서비스·API 함수는 가짜 모듈로 바꿔 끼운다 (도구가 함수 안에서 import하므로 sys.modules를 바꾸면 된다).

"""

import json
import sys
import types

import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from hri.domain.schemas import AgentState, EcosExchangeRate, EcosQuery, Indicator, MergedReport, ReportSummary, VerifyResult
from hri.exception.errors import ExternalApiError, OutputFormatError
from hri.router import tool_router
from hri.router.tool_router import (
	NO_ECOS_DATA_MESSAGE,
	NO_ECOS_QUERY_MESSAGE,
	NO_EXCHANGE_RATE_MESSAGE,
	NO_REPORTS_MESSAGE,
	TOOLS,
)

REPORT_TOOLS = ["summarize_reports", "verify_and_expand", "merge_reports", "find_usable_data"]


def fake_module(monkeypatch, name, **attrs):
	"""sys.modules에 가짜 모듈을 끼운다 (테스트가 끝나면 pytest가 되돌린다)."""
	module = types.ModuleType(name)
	for key, value in attrs.items():
		setattr(module, key, value)
	monkeypatch.setitem(sys.modules, name, module)
	return module


def run_graph(call, report_ids):
	"""ToolNode(TOOLS) 하나만 있는 작은 그래프로 도구 호출 1개를 실행하고 ToolMessage를 돌려준다.

	실제 에이전트 그래프와 같은 방식이다: AgentState.report_ids가 InjectedState로 도구에 주입된다.
	(ToolNode를 그래프 밖에서 바로 부르면 실행 설정이 없어 오류가 난다)
	"""
	graph = StateGraph(AgentState)
	graph.add_node("tools", ToolNode(TOOLS))
	graph.add_edge(START, "tools")
	graph.add_edge("tools", END)
	state = {"messages": [AIMessage(content="", tool_calls=[call])], "report_ids": list(report_ids)}
	return graph.compile().invoke(state)["messages"][-1]


def run_tool(name, args, report_ids=(12, 7)):
	"""도구 1개를 실행하고 결과 문자열을 돌려준다."""
	message = run_graph({"name": name, "args": args, "id": "call_1"}, report_ids)
	assert message.name == name
	return message.content


def indicator(name="경제성장률", period="2025"):
	return Indicator(name=name, value=2.0, unit="%", period=period, source="text")


# [확인 1] 도구 6개, 순서와 이름이 명세서 TOOLS 행과 같고 모두 설명이 있다 (LLM이 설명을 보고 고른다)
def test_tools_list():
	assert [t.name for t in TOOLS] == [
		"summarize_reports", "verify_and_expand", "merge_reports",
		"find_usable_data", "get_ecos_statistic_data", "get_ecos_exchange_rate",
	]
	assert all(t.description.strip() for t in TOOLS)


# [확인 2] report_ids는 LLM이 보는 인자에 없다 (LLM이 보고서를 고르거나 바꾸지 못함)
@pytest.mark.parametrize("name", REPORT_TOOLS)
def test_report_ids_hidden_from_llm(name):
	tool = next(t for t in TOOLS if t.name == name)
	assert "report_ids" not in tool.tool_call_schema.model_json_schema().get("properties", {})


# [확인 3] LLM이 보는 인자: find_usable_data(question), 통계 조회(코드·기간), 환율(통화 3종)
def test_llm_visible_arguments():
	props = {t.name: set(t.tool_call_schema.model_json_schema()["properties"]) for t in TOOLS}
	assert props["find_usable_data"] == {"question"}
	assert props["verify_and_expand"] == {"request"}
	assert props["get_ecos_exchange_rate"] == {"currency"}
	assert {"stat_code", "cycle", "item_code", "start", "end"} <= props["get_ecos_statistic_data"]


# [확인 4] 선택한 보고서가 없으면 서비스를 부르지 않고 안내한다
@pytest.mark.parametrize("name, args", [
	("summarize_reports", {}), ("verify_and_expand", {"request": "검증"}),
	("merge_reports", {}), ("find_usable_data", {"question": "성장률"}),
])
def test_no_reports(name, args):
	assert run_tool(name, args, report_ids=()) == NO_REPORTS_MESSAGE


# [확인 5] 요약: 상태의 report_ids마다 summarize_report를 부르고, 한 건이 실패해도 나머지는 돌려준다
def test_summarize_reports(monkeypatch):
	def summarize_report(report_id):
		if report_id == 7:
			raise OutputFormatError("형식 오류")
		return ReportSummary(report_id=report_id, three_lines=["가", "나", "다"], sections=[])

	fake_module(monkeypatch, "hri.service.summary_service", summarize_report=summarize_report)
	result = json.loads(run_tool("summarize_reports", {}))

	assert [r["report_id"] for r in result] == [12, 7]
	assert result[0]["summary"]["three_lines"] == ["가", "나", "다"]
	assert result[1] == {"report_id": 7, "error": "요약을 만들지 못했습니다."}


# [확인 6] 병합: report_ids 전체를 한 번에 넘기고 MergedReport를 JSON으로 돌려준다
def test_merge_reports(monkeypatch):
	calls = []

	def merge(report_ids):
		calls.append(report_ids)
		return MergedReport(common=["공통"], differences=["차이"], conclusion="결론")

	fake_module(monkeypatch, "hri.prompt.summary_prompt", merge_reports=merge)
	result = json.loads(run_tool("merge_reports", {}))

	assert calls == [[12, 7]]
	assert result == {"common": ["공통"], "differences": ["차이"], "conclusion": "결론"}


# [확인 7] 검증·보완: 보고서마다 (report_id, request)로 부른다
def test_verify_and_expand(monkeypatch):
	calls = []

	def verify(report_id, request):
		calls.append((report_id, request))
		return VerifyResult(matches=["일치"])

	fake_module(monkeypatch, "hri.prompt.summary_prompt", verify_and_expand=verify)
	result = json.loads(run_tool("verify_and_expand", {"request": "성장률 확인"}))

	assert calls == [(12, "성장률 확인"), (7, "성장률 확인")]
	assert [r["report_id"] for r in result] == [12, 7]
	assert result[0]["result"]["matches"] == ["일치"]


# [확인 8] 아직 없는 함수(김희진 3.4.7·3.4.8 미병합)는 멈추지 않고 '[미구현]'을 돌려준다
@pytest.mark.parametrize("name, args", [("merge_reports", {}), ("verify_and_expand", {"request": "검증"})])
def test_not_implemented(monkeypatch, name, args):
	monkeypatch.setitem(sys.modules, "hri.prompt.summary_prompt", None)  # import하면 ImportError
	assert "[미구현]" in run_tool(name, args)


# [확인 9] ECOS 활용 데이터 판단: 모든 보고서의 지표를 모아 한 번에 판단하고 조회 조건을 돌려준다
def test_find_usable_data(monkeypatch):
	seen = {}
	query = EcosQuery(stat_code="200Y102", cycle="A", item_code="10111", start="2025", end="2025", indicator_name="경제성장률")

	def find(indicators):
		seen["names"] = [i.name for i in indicators]
		return query

	fake_module(monkeypatch, "hri.service.indicator_service",
		extract_indicators=lambda report_id: [indicator(f"지표{report_id}")])
	fake_module(monkeypatch, "hri.api.ecos_finder", find_usable_data=find)
	result = json.loads(run_tool("find_usable_data", {"question": "성장률 확인"}))

	assert seen["names"] == ["지표12", "지표7"]
	assert result["stat_code"] == "200Y102" and result["indicator_name"] == "경제성장률"


# [확인 10] 쓸 만한 통계가 없거나 지표가 없으면 안내 문구, ECOS 오류는 오류 안내
def test_find_usable_data_none_and_error(monkeypatch):
	fake_module(monkeypatch, "hri.service.indicator_service", extract_indicators=lambda report_id: [indicator()])
	fake_module(monkeypatch, "hri.api.ecos_finder", find_usable_data=lambda indicators: None)
	assert run_tool("find_usable_data", {"question": "q"}) == NO_ECOS_QUERY_MESSAGE

	fake_module(monkeypatch, "hri.service.indicator_service", extract_indicators=lambda report_id: [])
	assert run_tool("find_usable_data", {"question": "q"}) == NO_ECOS_QUERY_MESSAGE

	def broken(indicators):
		raise ExternalApiError("ECOS 통계표 목록을 불러오지 못했습니다.")

	fake_module(monkeypatch, "hri.service.indicator_service", extract_indicators=lambda report_id: [indicator()])
	fake_module(monkeypatch, "hri.api.ecos_finder", find_usable_data=broken)
	assert run_tool("find_usable_data", {"question": "q"}) == "ECOS 통계표 목록을 불러오지 못했습니다."


# [확인 11] ECOS 통계 조회: 인자로 EcosQuery를 만들어 fetch_indicators에 (query, 지표 이름)으로 넘긴다
def test_get_ecos_statistic_data(monkeypatch):
	seen = {}

	def fetch(query, name):
		seen.update(query=query, name=name)
		return [indicator(period="2025")]

	fake_module(monkeypatch, "hri.api.ecos_client", fetch_indicators=fetch)
	args = {"stat_code": "200Y102", "cycle": "A", "item_code": "10111", "start": "2025", "end": "2025", "indicator_name": "경제성장률"}
	result = json.loads(run_tool("get_ecos_statistic_data", args))

	assert seen["name"] == "경제성장률"
	assert (seen["query"].stat_code, seen["query"].cycle, seen["query"].item_code2) == ("200Y102", "A", None)
	assert result[0]["period"] == "2025"


# [확인 12] 통계 값이 없으면 안내, ECOS 오류는 안내 문구만 (오류 내용·키는 돌려주지 않는다)
def test_get_ecos_statistic_data_empty_and_errors(monkeypatch):
	args = {"stat_code": "200Y102", "cycle": "A", "item_code": "10111", "start": "2025", "end": "2025"}

	fake_module(monkeypatch, "hri.api.ecos_client", fetch_indicators=lambda query, name: [])
	assert run_tool("get_ecos_statistic_data", args) == NO_ECOS_DATA_MESSAGE

	def api_error(query, name):
		raise ExternalApiError("ECOS API 호출 실패")

	fake_module(monkeypatch, "hri.api.ecos_client", fetch_indicators=api_error)
	assert run_tool("get_ecos_statistic_data", args) == "ECOS API 호출 실패"

	def unexpected(query, name):
		raise RuntimeError("secret-key-123")

	fake_module(monkeypatch, "hri.api.ecos_client", fetch_indicators=unexpected)
	result = run_tool("get_ecos_statistic_data", args)
	assert result == tool_router.ECOS_FAIL_MESSAGE and "secret" not in result


# [확인 13] 환율: 통화 1개로 get_exchange_rates를 부르고 값·기준일을 돌려준다. 값이 없으면 안내
def test_get_ecos_exchange_rate(monkeypatch):
	seen = []

	def rates(currencies):
		seen.append(currencies)
		return [EcosExchangeRate(currency="USD", value=1324.6, base_date="20261028")]

	fake_module(monkeypatch, "hri.api.ecos_client", get_exchange_rates=rates)
	result = json.loads(run_tool("get_ecos_exchange_rate", {"currency": "USD"}))
	assert seen == [["USD"]]
	assert result == [{"currency": "USD", "value": 1324.6, "base_date": "20261028"}]

	fake_module(monkeypatch, "hri.api.ecos_client", get_exchange_rates=lambda currencies: [])
	assert run_tool("get_ecos_exchange_rate", {"currency": "JPY"}) == NO_EXCHANGE_RATE_MESSAGE


# [확인 14] 통화는 USD·JPY·EUR만 받는다 (LLM이 다른 값을 넣으면 도구를 실행하지 않고 오류로 돌려준다)
def test_exchange_rate_rejects_unknown_currency():
	message = run_graph({"name": "get_ecos_exchange_rate", "args": {"currency": "GBP"}, "id": "c"}, [])
	assert message.status == "error"


# [확인 15] 에이전트 그래프가 진짜 TOOLS를 읽는다 ([미구현] 로그 없이 6개)
def test_agent_graph_loads_tools():
	from hri.graph.agent_graph import load_tools

	assert [t.name for t in load_tools()] == [t.name for t in TOOLS]