"""에이전트 그래프 테스트 · build_agent_graph, agent_node (엄정현, WBS 3.8.7)

agent ↔ tools 루프가 명세대로 도는지 확인한다 (네트워크·DB·LLM·.env 없이 실행).
- LLM: tests/fakes.py의 FakeToolChatModel (미리 넣은 AIMessage(tool_calls)를 차례로 돌려줌)
- 도구: 진짜 @tool 함수 2개를 가짜 hri.router.tool_router.TOOLS로 끼운다 (report_ids는 InjectedState로 주입)
- 보고서 목록: 가짜 hri.dao.report_dao.select_reports

실행: poetry run python -m pytest tests/test_agent_graph.py -v
"""

import logging
import sys
import types
from datetime import date
from typing import Annotated

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import InjectedState

from hri.domain.schemas import Report
from hri.graph import registry
from hri.graph.agent_graph import NO_REPORTS_TEXT, TOOLS_NOT_IMPLEMENTED
from tests.fakes import FakeToolChatModel


# ─────────────────────────────────────────────────────────────
# 가짜 부품
# ─────────────────────────────────────────────────────────────

# 테스트용 도구: report_ids는 LLM이 아니라 그래프 상태(AgentState.report_ids)에서 주입된다 (명세서 TOOLS 행)
@tool
def summarize_reports(report_ids: Annotated[list[int], InjectedState("report_ids")]) -> str:
	"""선택한 보고서들을 요약한다."""
	return f"요약 완료: {report_ids}"


@tool
def merge_reports(report_ids: Annotated[list[int], InjectedState("report_ids")]) -> str:
	"""선택한 보고서들을 병합한다."""
	return f"병합 완료: {report_ids}"


TEST_TOOLS = [summarize_reports, merge_reports]

REPORTS = [
	Report(report_id=12, title="2026년 경제전망", publisher="현대경제연구원", published_date=date(2026, 7, 31),
		file_path="data/raw/a.pdf", uploaded_by="tester"),
	Report(report_id=7, title="물가 동향", file_path="data/raw/b.pdf", uploaded_by="tester"),
]


def tool_call(name, call_id):
	return AIMessage(content="", tool_calls=[{"name": name, "args": {}, "id": call_id}])


class Deps:
	"""가짜 부품과 호출 기록을 한곳에 모은다."""

	def __init__(self):
		self.model = FakeToolChatModel()
		self.get_chat_model_kwargs = []
		self.select_reports_calls = []

	def get_chat_model(self, **kwargs):
		self.get_chat_model_kwargs.append(kwargs)
		return self.model

	def select_reports(self, keyword):
		self.select_reports_calls.append(keyword)
		return REPORTS


def install(monkeypatch, deps, tools=TEST_TOOLS):
	"""llm_client·report_dao·tool_router를 가짜 모듈로 바꿔 끼운다 (tools=None이면 TOOLS가 없는 tool_router)."""
	llm_client = types.ModuleType("hri.llm.llm_client")
	llm_client.get_chat_model = deps.get_chat_model
	report_dao = types.ModuleType("hri.dao.report_dao")
	report_dao.select_reports = deps.select_reports
	tool_router = types.ModuleType("hri.router.tool_router")
	if tools is not None:
		tool_router.TOOLS = tools
	for name, module in [("hri.llm.llm_client", llm_client), ("hri.dao.report_dao", report_dao), ("hri.router.tool_router", tool_router)]:
		monkeypatch.setitem(sys.modules, name, module)


@pytest.fixture
def deps(monkeypatch):
	registry.get_agent_graph.cache_clear()
	d = Deps()
	install(monkeypatch, d)
	yield d
	registry.get_agent_graph.cache_clear()


def run_nodes(inputs, config=None):
	graph = registry.get_agent_graph()
	return [name for update in graph.stream(inputs, config=config, stream_mode="updates") for name in update]


# ─────────────────────────────────────────────────────────────
# 테스트
# ─────────────────────────────────────────────────────────────

# [확인 1] 도구 호출이 없으면 agent 한 번으로 끝 (tools_condition → END)
#          모델은 get_chat_model(temperature=0)으로 만들고 TOOLS를 bind_tools로 붙인다
def test_agent_ends_without_tool_calls(deps):
	deps.model.responses = [AIMessage(content="도구 없이 답합니다")]
	inputs = {"messages": [HumanMessage("안녕")], "report_ids": [12]}

	assert run_nodes(inputs) == ["agent"]
	assert deps.get_chat_model_kwargs == [{"temperature": 0}]
	assert [t.name for t in deps.model.bound_tools] == ["summarize_reports", "merge_reports"]


# [확인 2] 도구 루프: agent(tool_calls) → tools → agent(최종 답변) → END
#          ToolMessage에 그래프 상태의 report_ids가 주입되고, LLM이 보는 도구 인자에는 report_ids가 없다
def test_agent_tool_loop_injects_report_ids(deps):
	deps.model.responses = [tool_call("summarize_reports", "call_1"), AIMessage(content="요약했습니다")]
	graph = registry.get_agent_graph()
	result = graph.invoke({"messages": [HumanMessage("요약해줘")], "report_ids": [12, 7]})

	assert [type(m) for m in result["messages"]] == [HumanMessage, AIMessage, ToolMessage, AIMessage]
	tool_message = result["messages"][2]
	assert tool_message.name == "summarize_reports" and tool_message.content == "요약 완료: [12, 7]"
	assert result["messages"][-1].content == "요약했습니다"
	assert "report_ids" not in summarize_reports.tool_call_schema.model_json_schema().get("properties", {})


# [확인 3] '검증 후 병합'처럼 도구를 여러 번 차례로 호출할 수 있다
def test_agent_calls_tools_in_sequence(deps):
	deps.model.responses = [tool_call("summarize_reports", "call_1"), tool_call("merge_reports", "call_2"), AIMessage(content="끝")]
	nodes = run_nodes({"messages": [HumanMessage("요약하고 병합해줘")], "report_ids": [12]})
	assert nodes == ["agent", "tools", "agent", "tools", "agent"]


# [확인 4] system 메시지: 매 호출 맨 앞에 1개만 붙고(선택 보고서 목록 포함), state에는 저장되지 않는다
#          DB에 없는 번호(99)는 번호만 적는다
def test_agent_system_message(deps):
	deps.model.responses = [tool_call("summarize_reports", "call_1"), AIMessage(content="끝")]
	result = registry.get_agent_graph().invoke({"messages": [HumanMessage("요약해줘")], "report_ids": [12, 7, 99]})

	assert len(deps.model.calls) == 2
	for messages in deps.model.calls:
		assert isinstance(messages[0], SystemMessage)
		assert sum(isinstance(m, SystemMessage) for m in messages) == 1
	system_text = deps.model.calls[0][0].content
	assert "- [12] 2026년 경제전망 (현대경제연구원, 2026-07-31)" in system_text
	assert "- [7] 물가 동향" in system_text
	assert "- [99]" in system_text
	assert deps.select_reports_calls[0] == ""
	assert not any(isinstance(m, SystemMessage) for m in result["messages"])


# [확인 5] 선택한 보고서가 없으면 NO_REPORTS_TEXT를 넣고 DB를 조회하지 않는다
def test_agent_without_reports(deps):
	deps.model.responses = [AIMessage(content="보고서를 선택해 주세요")]
	run_nodes({"messages": [HumanMessage("요약해줘")], "report_ids": []})
	assert NO_REPORTS_TEXT in deps.model.calls[0][0].content
	assert deps.select_reports_calls == []


# [확인 6] recursion_limit=8(run_agent_with_tools가 넘김)이면 도구 3번 + 최종 답변까지 끝난다 (agent·tools 각각 1단계, 7단계)
#          명세서 run_agent_with_tools 행의 'recursion_limit=8이면 약 3회'와 같다
def test_agent_three_tool_calls_within_limit(deps):
	deps.model.responses = [tool_call("summarize_reports", f"call_{i}") for i in range(3)] + [AIMessage(content="끝")]
	result = registry.get_agent_graph().invoke(
		{"messages": [HumanMessage("세 번 요약해줘")], "report_ids": [12]},
		config={"recursion_limit": 8},
	)
	assert sum(isinstance(m, ToolMessage) for m in result["messages"]) == 3
	assert result["messages"][-1].content == "끝"


# [확인 9] 도구 호출이 끝나지 않으면 recursion_limit에서 GraphRecursionError (run_agent_with_tools가 잡아 '요청을 처리하지 못했습니다')
#          recursion_limit=8이면 agent 4번·tools 4번(8단계)을 실행한 뒤 멈춘다
def test_agent_stops_at_recursion_limit(deps):
	deps.model.responses = [tool_call("summarize_reports", f"call_{i}") for i in range(10)]
	executed = []
	graph = registry.get_agent_graph()
	with pytest.raises(GraphRecursionError):
		for update in graph.stream(
			{"messages": [HumanMessage("계속 요약해줘")], "report_ids": [12]},
			config={"recursion_limit": 8},
			stream_mode="updates",
		):
			executed.extend(update)
	assert executed.count("tools") == 4 and executed.count("agent") == 4


# [확인 7] TOOLS가 아직 없으면(김희진 3.7.7 미구현) 오류 로그에 [미구현]을 남기고, 도구 없이 그래프를 만들어 실행한다
def test_agent_without_tools_logs_not_implemented(monkeypatch, caplog):
	registry.get_agent_graph.cache_clear()
	d = Deps()
	install(monkeypatch, d, tools=None)
	d.model.responses = [AIMessage(content="도구가 없습니다")]

	with caplog.at_level(logging.ERROR, logger="hri.graph.agent_graph"):
		nodes = run_nodes({"messages": [HumanMessage("요약해줘")], "report_ids": [12]})
	registry.get_agent_graph.cache_clear()

	assert nodes == ["agent"]
	assert TOOLS_NOT_IMPLEMENTED in caplog.text
	assert d.model.bound_tools == []          # 도구가 없으면 bind_tools를 부르지 않음


# [확인 8] FakeToolChatModel: with_structured_output은 미리 넣은 Pydantic 객체를 돌려주고, 응답이 떨어지면 알려 준다
def test_fake_model_structured_output():
	from hri.domain.schemas import RouteDecision

	fake = FakeToolChatModel(responses=[RouteDecision(route="chat")])
	assert fake.with_structured_output(RouteDecision).invoke([HumanMessage("ㅇㅇ")]).route == "chat"
	assert fake.structured_schemas == [RouteDecision]
	with pytest.raises(AssertionError):
		fake.invoke("응답이 없음")
