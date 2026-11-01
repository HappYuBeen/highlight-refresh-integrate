"""에이전트 실행 테스트 · run_agent_with_tools (WBS 3.7.10)

네트워크·DB·LLM·.env 없이 실행한다.
- 단위 테스트: 가짜 그래프(FakeGraph)로 입력·설정·사용량 기록·오류 처리를 확인한다.
- 연결 테스트: 진짜 agent_graph·registry·TOOLS를 쓰고, LLM(FakeToolChatModel)·DB·서비스만 가짜로 바꾼다.
- 서비스·DAO·설정은 함수 안에서 import하므로 sys.modules에 가짜 모듈을 끼운다.

실행: poetry run python -m pytest tests/test_run_agent.py -v
"""

import logging
import sys
import types
from types import SimpleNamespace

import pytest
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from hri.domain.schemas import Report, ReportSummary
from hri.exception.errors import InvalidInputError
from hri.graph import registry
from hri.router import tool_router
from hri.router.tool_router import AGENT_FAIL_MESSAGE, run_agent_with_tools
from tests.fakes import FakeToolChatModel

REPORTS = [
	Report(report_id=12, title="2026년 경제전망", file_path="data/raw/a.pdf", uploaded_by="tester"),
	Report(report_id=7, title="물가 동향", file_path="data/raw/b.pdf", uploaded_by="tester"),
]


def fake_module(monkeypatch, name, **attrs):
	"""sys.modules에 가짜 모듈을 끼운다 (테스트가 끝나면 pytest가 되돌린다)."""
	module = types.ModuleType(name)
	for key, value in attrs.items():
		setattr(module, key, value)
	monkeypatch.setitem(sys.modules, name, module)


class Recorder:
	"""가짜 부품들의 호출 기록과 동작을 한곳에 모은다."""

	def __init__(self):
		self.inserted = []
		self.limit = 8
		self.usd_krw_calls = 0
		self.usd_krw_error = None
		self.insert_error = None

	def load_settings(self):
		return SimpleNamespace(graph_recursion_limit=self.limit)

	def get_usd_krw(self):
		self.usd_krw_calls += 1
		if self.usd_krw_error:
			raise self.usd_krw_error
		return 1400.0

	def cost_korean_won(self, usage, usd_to_krw):
		tokens = next(iter(usage.values()))
		return tokens["total_tokens"] * 0.5

	def insert_usage(self, usage):
		if self.insert_error:
			raise self.insert_error
		self.inserted.append(usage)
		return len(self.inserted)


@pytest.fixture
def rec(monkeypatch):
	"""설정·사용량 기록·환율을 가짜로 바꾼다. 연결 테스트에서도 함께 쓴다."""
	r = Recorder()
	fake_module(monkeypatch, "hri.config.settings", load_settings=r.load_settings)
	fake_module(monkeypatch, "hri.dao.usage_dao", insert_usage=r.insert_usage)
	fake_module(monkeypatch, "hri.llm.llm_client", get_usd_krw=r.get_usd_krw, cost_korean_won=r.cost_korean_won)
	return r


class FakeGraph:
	"""invoke 호출을 기록하고 미리 정한 결과(또는 예외)를 돌려주는 가짜 그래프."""

	def __init__(self, result=None, error=None, usage=None):
		self.result, self.error, self.usage = result, error, usage
		self.calls = []

	def invoke(self, inputs, config=None):
		self.calls.append((inputs, config))
		if self.usage:  # 그래프가 도는 동안 모델이 쓴 토큰을 사용량 핸들러가 모은 것처럼 채운다
			config["callbacks"][0].usage_metadata.update(self.usage)
		if self.error:
			raise self.error
		return self.result


def install_graph(monkeypatch, graph):
	fake_module(monkeypatch, "hri.graph.registry", get_agent_graph=lambda: graph)


def ai(text="답변입니다"):
	return AIMessage(content=text)


USAGE = {"gpt-5-mini": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}}


# ─────────────────────────────────────────────────────────────
# 단위 테스트 (가짜 그래프)
# ─────────────────────────────────────────────────────────────
# [확인 1] 요청(HumanMessage)과 report_ids를 넣어 invoke하고 마지막 답변을 돌려준다. 반복 한도는 설정에서 읽는다
def test_invoke_inputs_and_answer(monkeypatch, rec):
	rec.limit = 5
	graph = FakeGraph(result={"messages": [HumanMessage("요청"), ToolMessage("도구 결과", name="x", tool_call_id="1"), ai("  최종 답변  ")]})
	install_graph(monkeypatch, graph)

	assert run_agent_with_tools("  성장률 요약해줘  ", [12, 7]) == "최종 답변"

	inputs, config = graph.calls[0]
	assert [m.content for m in inputs["messages"]] == ["성장률 요약해줘"]
	assert isinstance(inputs["messages"][0], HumanMessage)
	assert inputs["report_ids"] == [12, 7]
	assert config["recursion_limit"] == 5
	assert isinstance(config["callbacks"][0], UsageMetadataCallbackHandler)


# [확인 2] 요청이 비어 있으면 그래프를 부르지 않고 InvalidInputError
@pytest.mark.parametrize("request_text", ["", "   ", "\n"])
def test_empty_request(monkeypatch, rec, request_text):
	graph = FakeGraph(result={"messages": [ai()]})
	install_graph(monkeypatch, graph)
	with pytest.raises(InvalidInputError):
		run_agent_with_tools(request_text, [12])
	assert graph.calls == []


# [확인 3] 반복 한도(GraphRecursionError)에 걸리면 안내 문구를 돌려주고, 이미 쓴 토큰은 기록한다
def test_recursion_limit(monkeypatch, rec):
	install_graph(monkeypatch, FakeGraph(error=GraphRecursionError("limit"), usage=USAGE))

	assert run_agent_with_tools("요청", [12]) == AGENT_FAIL_MESSAGE
	assert len(rec.inserted) == 1


# [확인 4] 사용량은 모델마다 llm_usage에 저장한다 (flow='agent', user_id·ref_id 없음, 원화 비용은 참고용)
def test_usage_recorded(monkeypatch, rec):
	usage = {**USAGE, "gpt-4o-mini": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}
	install_graph(monkeypatch, FakeGraph(result={"messages": [ai()]}, usage=usage))
	run_agent_with_tools("요청", [12])

	assert [(u.flow, u.user_id, u.ref_id, u.model, u.input_tokens, u.output_tokens) for u in rec.inserted] == [
		("agent", None, None, "gpt-5-mini", 100, 20),
		("agent", None, None, "gpt-4o-mini", 10, 5),
	]
	assert rec.inserted[0].cost_krw == 60.0
	assert rec.usd_krw_calls == 1


# [확인 5] AI를 부르지 않았으면(사용량 없음) 기록도 환율 조회도 하지 않는다
def test_no_usage(monkeypatch, rec):
	install_graph(monkeypatch, FakeGraph(result={"messages": [ai()]}))
	run_agent_with_tools("요청", [12])
	assert rec.inserted == [] and rec.usd_krw_calls == 0


# [확인 6] 환율·DB 오류는 로그만 남기고 이미 만든 답은 그대로 돌려준다
@pytest.mark.parametrize("which", ["usd_krw_error", "insert_error"])
def test_usage_errors_are_swallowed(monkeypatch, rec, caplog, which):
	setattr(rec, which, RuntimeError("실패"))
	install_graph(monkeypatch, FakeGraph(result={"messages": [ai("그대로 답변")]}, usage=USAGE))

	with caplog.at_level(logging.ERROR):
		assert run_agent_with_tools("요청", [12]) == "그대로 답변"
	assert "에이전트 사용량 기록 실패" in caplog.text


# [확인 7] 답변 글이 없으면 안내 문구: 마지막 AIMessage가 비었거나(도구 호출만 한 경우) AIMessage가 없을 때
@pytest.mark.parametrize("messages", [
	[HumanMessage("요청"), AIMessage(content="")],
	[HumanMessage("요청")],
	[],
])
def test_no_answer(monkeypatch, rec, messages):
	install_graph(monkeypatch, FakeGraph(result={"messages": messages}))
	assert run_agent_with_tools("요청", [12]) == AGENT_FAIL_MESSAGE


# [확인 8] 모델이 글 조각 목록(content=[{'type':'text', ...}])으로 답해도 글만 이어 붙인다
def test_list_content(monkeypatch, rec):
	content = [{"type": "text", "text": "첫째 "}, {"type": "image", "url": "x"}, {"type": "text", "text": "둘째"}]
	install_graph(monkeypatch, FakeGraph(result={"messages": [AIMessage(content=content)]}))
	assert run_agent_with_tools("요청", [12]) == "첫째 둘째"


# [확인 9] 실행한 도구 이름을 INFO 로그로 남긴다
def test_logs_tool_names(monkeypatch, rec, caplog):
	messages = [HumanMessage("요청"), ToolMessage("r", name="summarize_reports", tool_call_id="1"), ai()]
	install_graph(monkeypatch, FakeGraph(result={"messages": messages}))

	with caplog.at_level(logging.INFO, logger=tool_router.logger.name):
		run_agent_with_tools("요청", [12])
	assert "['summarize_reports']" in caplog.text


# ─────────────────────────────────────────────────────────────
# 연결 테스트 (진짜 agent_graph·registry·TOOLS)
# ─────────────────────────────────────────────────────────────
@pytest.fixture
def real_graph(monkeypatch, rec):
	"""진짜 에이전트 그래프에 가짜 모델·보고서 목록·요약 서비스를 끼운다. (가짜 모델, 요약 호출 기록)을 돌려준다."""
	model = FakeToolChatModel()
	summarize_calls = []

	def summarize_report(report_id):
		summarize_calls.append(report_id)
		return ReportSummary(report_id=report_id, three_lines=["가", "나", "다"], sections=[])

	fake_module(monkeypatch, "hri.llm.llm_client", get_chat_model=lambda **kwargs: model,
		get_usd_krw=rec.get_usd_krw, cost_korean_won=rec.cost_korean_won)
	fake_module(monkeypatch, "hri.dao.report_dao", select_reports=lambda keyword: REPORTS)
	fake_module(monkeypatch, "hri.service.summary_service", summarize_report=summarize_report)
	registry.get_agent_graph.cache_clear()
	yield model, summarize_calls
	registry.get_agent_graph.cache_clear()


def tool_call(name, call_id, args=None):
	return AIMessage(content="", tool_calls=[{"name": name, "args": args or {}, "id": call_id}])


# [확인 10] 도구를 부른 뒤 최종 답변을 돌려준다. report_ids는 LLM이 아니라 상태에서 주입돼 도구에 전달된다
def test_agent_calls_tool_then_answers(real_graph, rec):
	model, summarize_calls = real_graph
	model.responses = [tool_call("summarize_reports", "call_1"), ai("요약했습니다")]

	assert run_agent_with_tools("보고서 요약해줘", [12, 7]) == "요약했습니다"

	assert summarize_calls == [12, 7]
	system, user = model.calls[0]
	assert "2026년 경제전망" in system.content and user.content == "보고서 요약해줘"
	tool_result = model.calls[1][-1]
	assert tool_result.name == "summarize_reports" and '"report_id": 12' in tool_result.content


# [확인 11] 도구를 계속 부르며 한도를 넘으면 진짜 그래프도 안내 문구를 돌려준다
def test_agent_recursion_limit_real(real_graph, rec):
	model, _ = real_graph
	rec.limit = 3
	model.responses = [tool_call("summarize_reports", f"call_{i}") for i in range(10)]

	assert run_agent_with_tools("요약해줘", [12]) == AGENT_FAIL_MESSAGE


# [확인 12] 모델이 알려 준 토큰 수가 핸들러를 거쳐 llm_usage 기록까지 이어진다
def test_agent_usage_end_to_end(real_graph, rec):
	model, _ = real_graph
	message = AIMessage(content="끝", response_metadata={"model_name": "gpt-5-mini"},
		usage_metadata={"input_tokens": 50, "output_tokens": 10, "total_tokens": 60})
	model.responses = [message]

	assert run_agent_with_tools("요청", [12]) == "끝"
	assert [(u.flow, u.model, u.input_tokens, u.output_tokens) for u in rec.inserted] == [("agent", "gpt-5-mini", 50, 10)]
