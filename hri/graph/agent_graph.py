"""Tool Calling 흐름 · build_agent_graph, agent_node (엄정현)

[3.8.7] 에이전트 그래프를 명세대로 채웠다 (LangGraph 워크플로우 명세서 build_agent_graph·agent_node 행).

흐름
	START → agent → tools_condition
		├─ 마지막 AIMessage에 tool_calls가 있으면 'tools' → tools(ToolNode(TOOLS)) → agent (반복)
		└─ 없으면 END
- agent_node: system 메시지(선택 보고서 목록·도구 사용 규칙) + state['messages']로
  get_chat_model(temperature=0).bind_tools(TOOLS).invoke() → {'messages': [AIMessage]}
- recursion_limit은 run_agent_with_tools()(김희진 3.7.10)가 config로 넘긴다. 넘으면 GraphRecursionError가 나고,
  run_agent_with_tools가 잡아 '요청을 처리하지 못했습니다'를 보여 준다.
- compile은 registry.get_agent_graph()가 한 번만 한다.

system 메시지는 agent_node가 매번 맨 앞에 붙이고 state에는 저장하지 않는다 (2026-10-27 결정).
그래서 run_agent_with_tools()는 입력 messages에 user 메시지만 넣으면 된다.
"""

import logging

from langchain_core.messages import SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from hri.domain.schemas import AgentState

logger = logging.getLogger(__name__)

# TODO(확인 필요): 도구 사용 규칙 문구. 명세서에는 'system 메시지(선택 보고서 목록·도구 사용 규칙)'만 있고 문구가 없다.
#   아래 규칙은 명세서의 TOOLS 행(report_ids는 InjectedState로 주입, LLM이 보고서를 고르거나 바꾸지 못함)과
#   run_agent_with_tools 행('검증 후 병합'처럼 여러 도구를 연속 호출)을 옮긴 것이다. 문구가 정해지면 이 값만 바꾼다.
AGENT_SYSTEM_PROMPT = (
	"너는 경제 분석 보고서 서비스의 요청 처리 도우미다. 사용자의 요청을 처리하려고 필요한 도구를 골라 호출한다.\n"
	"[선택한 보고서]\n{reports}\n"
	"[도구 사용 규칙]\n"
	"- 보고서 내용이 필요하면 도구를 호출하고, 도구 결과에 있는 내용만으로 한국어로 답한다.\n"
	"- 보고서 번호(report_ids)는 도구에 자동으로 전달되므로 직접 고르거나 바꾸지 않는다.\n"
	"- '검증 후 병합'처럼 여러 단계가 필요한 요청은 도구를 차례로 호출한다.\n"
	"- 도구로 처리할 수 없는 요청이면 도구를 호출하지 않고 처리할 수 없다고 답한다."
)

# 선택한 보고서가 없을 때 [선택한 보고서] 자리에 넣는 문구
NO_REPORTS_TEXT = "- 선택한 보고서 없음"

# TOOLS가 아직 없을 때 남기는 오류 문구
TOOLS_NOT_IMPLEMENTED = (
	"[미구현] TOOLS(hri/router/tool_router.py, 김희진 3.7.7)가 아직 구현되지 않았습니다. "
	"도구 없이 에이전트 그래프를 만듭니다 (도구 호출 불가)."
)


def load_tools() -> list:
	"""hri.router.tool_router의 TOOLS를 가져온다.

	[미구현 대비] TOOLS가 아직 없으면 오류 로그(TOOLS_NOT_IMPLEMENTED)를 남기고 빈 목록을 돌려준다.
	그래프·앱 예열(main)·다른 그래프 테스트가 멈추지 않게 하려는 임시 처리다. TOOLS가 들어오면 그대로 쓴다.
	함수 안에서 import한다: 도구가 서비스(summarize_report 등)를 부르고 서비스가 registry를 import하므로 순환 import 방지.
	"""
	try:
		from hri.router.tool_router import TOOLS
	except ImportError:
		logger.error(TOOLS_NOT_IMPLEMENTED)
		return []
	return list(TOOLS)


def format_reports(report_ids: list[int]) -> str:
	"""system 메시지의 [선택한 보고서] 목록을 만든다. 예) '- [12] 2026년 경제전망 (현대경제연구원, 2026-07-31)'

	report_dao.select_reports('')(장유빈, 키워드 ''면 전체 조회)로 보고서를 읽어 report_ids에 있는 것만 고른다.
	id로 조회하는 DAO 함수가 따로 없어서 이 방법을 쓴다 (2026-10-27 결정). DB에서 못 찾은 번호는 번호만 적는다.
	"""
	if not report_ids:
		return NO_REPORTS_TEXT

	from hri.dao.report_dao import select_reports

	found = {r.report_id: r for r in select_reports("")}
	lines = []
	for report_id in report_ids:
		report = found.get(report_id)
		if report is None:
			lines.append(f"- [{report_id}]")
			continue
		extra = ", ".join(str(v) for v in (report.publisher, report.published_date) if v)
		lines.append(f"- [{report_id}] {report.title}" + (f" ({extra})" if extra else ""))
	return "\n".join(lines)


def agent_node(state: AgentState) -> dict:
	"""system 메시지 + state['messages']로 도구를 붙인 모델을 호출하고 {'messages': [AIMessage]}를 돌려준다.

	- system 메시지는 매번 새로 만들어 맨 앞에 붙이고 state에는 저장하지 않는다 (반복해도 1개만 들어감).
	- 모델: get_chat_model(temperature=0).bind_tools(TOOLS). TOOLS가 비었으면(미구현) bind_tools 없이 호출한다.
	- 돌려준 AIMessage는 add_messages 리듀서로 messages 끝에 붙는다.
	"""
	from hri.llm.llm_client import get_chat_model

	system = SystemMessage(AGENT_SYSTEM_PROMPT.format(reports=format_reports(state.get("report_ids") or [])))
	tools = load_tools()
	model = get_chat_model(temperature=0)
	if tools:
		model = model.bind_tools(tools)
	response = model.invoke([system, *state["messages"]])
	return {"messages": [response]}


def build_agent_graph() -> StateGraph:
	"""에이전트 그래프를 노드·엣지까지 연결해 돌려준다 (compile 전 StateGraph)."""
	graph = StateGraph(AgentState)

	graph.add_node("agent", agent_node)
	graph.add_node("tools", ToolNode(load_tools()))

	graph.add_edge(START, "agent")
	# tools_condition: 마지막 메시지에 tool_calls가 있으면 'tools', 없으면 END('__end__')
	graph.add_conditional_edges("agent", tools_condition, ["tools", END])
	graph.add_edge("tools", "agent")
	return graph
