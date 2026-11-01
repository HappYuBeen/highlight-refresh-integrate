"""그래프 상태(graph_state.py)가 명세대로 동작하는지 확인하는 테스트 (엄정현, 네트워크·DB·LLM 없이 실행).

노드는 실제 업무 함수 대신 고정 값을 돌려주는 lambda로 만들고, 리듀서가 값을 합치는지만 본다.
그래프 분기·통합 테스트(test_*_graph)는 test_graphs.py(3.8.9)에서 한다.
"""

import operator
import typing

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Send
from pydantic import BaseModel, TypeAdapter, ValidationError
from typing_extensions import is_typeddict

from hri.domain.schemas import (
	AgentState,
	IngestState,
	QAState,
	RouteDecision,
	SectionSummary,
	SummaryState,
	Table,
)
from hri.domain.schemas import graph_state as graph_state_module


# 병렬 노드 두 개(a ∥ b)가 끝나면 join 노드로 모이는 그래프를 만든다
def _build_parallel(state_cls, a_update: dict, b_update: dict):
	g = StateGraph(state_cls)
	g.add_node("a", lambda s: a_update)
	g.add_node("b", lambda s: b_update)
	g.add_node("join", lambda s: {})
	g.add_edge(START, "a")
	g.add_edge(START, "b")
	g.add_edge(["a", "b"], "join")
	g.add_edge("join", END)
	return g.compile()


# [확인 1] __all__에 5개 이름이 있고, hri.domain.schemas에서 바로 불러올 수 있어야 한다
def test_all_lists_five_names():
	assert set(graph_state_module.__all__) == {
		"IngestState", "QAState", "SummaryState", "AgentState", "RouteDecision",
	}


# [확인 2] 4개 State는 TypedDict, RouteDecision은 Pydantic BaseModel
def test_state_types():
	for state_cls in (IngestState, QAState, SummaryState, AgentState):
		assert is_typeddict(state_cls)
	assert issubclass(RouteDecision, BaseModel)


# [확인 3] 명세서 '그래프 상태' 행의 키 이름이 그대로 있어야 한다 (김희진·장유빈이 이 이름으로 읽음)
@pytest.mark.parametrize(
	("state_cls", "keys"),
	[
		(IngestState, {
			"file_path", "user_id", "meta", "report_id", "pages", "sections", "chunks", "charts",
			"tables", "summarized_tables", "warnings", "counts", "error",
		}),
		(QAState, {
			"messages", "question", "persona", "report_ids", "k", "use_mmr", "user_id", "route",
			"rewritten", "docs", "context_docs", "has_source", "answer", "sources", "suggestions",
			"terms", "started_at", "log_id", "error",
		}),
		(SummaryState, {"report_id", "sections", "section_summaries", "three_lines", "error"}),
		(AgentState, {"messages", "report_ids"}),
	],
)
def test_state_keys_match_spec(state_cls, keys):
	assert set(state_cls.__annotations__) == keys


# [확인 4] 리듀서가 Annotated 맨 마지막에 있어야 한다 (LangGraph가 마지막 값을 리듀서로 읽음)
@pytest.mark.parametrize(
	("state_cls", "key", "reducer"),
	[
		(IngestState, "tables", operator.add),
		(IngestState, "warnings", operator.add),
		(SummaryState, "section_summaries", operator.add),
		(QAState, "messages", add_messages),
		(AgentState, "messages", add_messages),
	],
)
def test_reducer_is_last_metadata(state_cls, key, reducer):
	hint = typing.get_type_hints(state_cls, include_extras=True)[key]
	assert hint.__metadata__[-1] is reducer


# [확인 5] 모든 State 필드에 Field(description=...)이 달려 있어야 한다 (TypeAdapter 스키마에 description이 나옴)
@pytest.mark.parametrize("state_cls", [IngestState, QAState, SummaryState, AgentState])
def test_every_field_has_description(state_cls):
	props = TypeAdapter(state_cls).json_schema()["properties"]
	missing = [key for key, schema in props.items() if not schema.get("description")]
	assert missing == []


# [확인 6] IngestState: 병렬 노드가 각자 반환한 tables·warnings가 덮어써지지 않고 합쳐져야 한다
def test_ingest_tables_and_warnings_are_merged():
	graph = _build_parallel(
		IngestState,
		{"tables": [Table(page=1)], "warnings": ["3쪽 스캔본"]},
		{"tables": [Table(page=2, source_type="chart")], "warnings": ["차트 인식 실패"]},
	)
	result = graph.invoke({"file_path": "data/raw/sample.pdf", "user_id": "tester"})

	assert sorted(t.page for t in result["tables"]) == [1, 2]
	assert sorted(result["warnings"]) == ["3쪽 스캔본", "차트 인식 실패"]


# [확인 7] IngestState: 리듀서가 없는 키(summarized_tables)를 병렬 노드가 동시에 쓰면 오류가 나야 한다
def test_ingest_key_without_reducer_rejects_parallel_write():
	graph = _build_parallel(
		IngestState,
		{"summarized_tables": [Table(page=1)]},
		{"summarized_tables": [Table(page=2)]},
	)
	with pytest.raises(Exception, match="summarized_tables"):
		graph.invoke({"file_path": "data/raw/sample.pdf", "user_id": "tester"})


# [확인 8] SummaryState: Send로 섹션마다 나눠 보낸 요약이 section_summaries에 모두 모여야 한다
def test_summary_send_fan_out_collects_all_sections():
	g = StateGraph(SummaryState)
	g.add_node("load_sections", lambda s: {"sections": ["서론", "본론", "결론"]})
	g.add_node(
		"summarize_section",
		lambda payload: {"section_summaries": [SectionSummary(title=payload["section"], summary="요약")]},
	)
	g.add_edge(START, "load_sections")
	g.add_conditional_edges(
		"load_sections",
		lambda s: [Send("summarize_section", {"section": sec}) for sec in s["sections"]],
		["summarize_section"],
	)
	g.add_edge("summarize_section", END)

	result = g.compile().invoke({"report_id": 1})

	assert sorted(x.title for x in result["section_summaries"]) == ["결론", "본론", "서론"]


# [확인 9] QAState: 같은 thread_id로 2턴 질문하면 messages가 쌓이고(4개), 다른 키는 이번 턴 값으로 바뀌어야 한다
def test_qa_messages_accumulate_across_turns():
	g = StateGraph(QAState)
	g.add_node(
		"generate",
		lambda s: {"answer": f"{s['question']} 답변", "messages": [HumanMessage(s["question"]), AIMessage(f"{s['question']} 답변")]},
	)
	g.add_edge(START, "generate")
	g.add_edge("generate", END)
	graph = g.compile(checkpointer=InMemorySaver())
	config = {"configurable": {"thread_id": "session-1"}}

	graph.invoke({"question": "1번 질문"}, config)
	result = graph.invoke({"question": "2번 질문"}, config)

	assert len(result["messages"]) == 4
	assert result["answer"] == "2번 질문 답변"


# [확인 10] QAState: thread_id가 다르면 대화 기록이 섞이지 않아야 한다
def test_qa_messages_isolated_by_thread():
	g = StateGraph(QAState)
	g.add_node("generate", lambda s: {"messages": [HumanMessage(s["question"]), AIMessage("답변")]})
	g.add_edge(START, "generate")
	g.add_edge("generate", END)
	graph = g.compile(checkpointer=InMemorySaver())

	graph.invoke({"question": "A"}, {"configurable": {"thread_id": "a"}})
	result = graph.invoke({"question": "B"}, {"configurable": {"thread_id": "b"}})

	assert len(result["messages"]) == 2


# [확인 11] AgentState: 입력 메시지 뒤에 agent 노드의 AIMessage가 이어 붙어야 한다
def test_agent_messages_appended():
	g = StateGraph(AgentState)
	g.add_node("agent", lambda s: {"messages": [AIMessage("도구 없이 답변")]})
	g.add_edge(START, "agent")
	g.add_edge("agent", END)

	result = g.compile().invoke({"messages": [HumanMessage("요약해줘")], "report_ids": [1, 2]})

	assert [type(m) for m in result["messages"]] == [HumanMessage, AIMessage]
	assert result["report_ids"] == [1, 2]


# [확인 12] RouteDecision: 'rewrite'·'chat'만 받고, 그 외 값은 오류
@pytest.mark.parametrize("route", ["rewrite", "chat"])
def test_route_decision_accepts_rewrite_and_chat(route):
	assert RouteDecision(route=route).route == route


@pytest.mark.parametrize("route", ["error", "rag", "no_answer", ""])
def test_route_decision_rejects_other_values(route):
	with pytest.raises(ValidationError):
		RouteDecision(route=route)


# [확인 13] RouteDecision: with_structured_output에 넘어가는 스키마에 route 설명이 있어야 한다
def test_route_decision_schema_has_description():
	schema = RouteDecision.model_json_schema()["properties"]["route"]
	assert schema["enum"] == ["rewrite", "chat"]
	assert "rewrite" in schema["description"] and "chat" in schema["description"]


# [확인 14] QAState.route: 답을 만든 노드가 적는 최종 경로 4개만 허용 (QALog.route·qa_logs.route ENUM과 같은 값)
def test_qa_route_values_match_qa_logs():
	hint = typing.get_type_hints(QAState, include_extras=True)["route"]
	assert set(typing.get_args(hint.__origin__)) == {"rag", "no_answer", "chat", "error"}


# [확인 15] 'rewrite'는 라우팅 함수(route_question)의 분기 값일 뿐 state에 저장되지 않는다
#           → QAState.route에는 없고 RouteDecision.route에만 있어야 한다
def test_rewrite_only_in_route_decision():
	qa_hint = typing.get_type_hints(QAState, include_extras=True)["route"]
	assert "rewrite" not in typing.get_args(qa_hint.__origin__)
	assert "rewrite" in typing.get_args(RouteDecision.model_fields["route"].annotation)