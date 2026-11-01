"""기록 조회·세션 복원 테스트 (임세준) - 커밋하지 않는 파일

진짜 get_history(), restore_session()은 그대로 쓰고,
DB(select_logs)와 질의응답 그래프(get_qa_graph)만 가짜로 바꿔서 확인한다.

실행 (레포 맨 위 폴더에서):
	poetry run python -m tests.try_history
"""
import sys
import types
from datetime import datetime, timedelta
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from hri.domain.schemas import QALog
from hri.service import history_service


# ----------------------------------------------------------------
# 1. 가짜 DB (qa_logs 대신 리스트)
# ----------------------------------------------------------------
now = datetime.now()


def make_log(log_id, user_id, session_id, question, answer, minutes_ago):
	return QALog(
		log_id=log_id, user_id=user_id, session_id=session_id,
		question=question, answer=answer, persona="실무자", route="rag",
		has_source=True, response_ms=1000, tokens=100, cost_krw=0.5,
		created_at=now - timedelta(minutes=minutes_ago)
	)


FAKE_DB = [
	make_log(1, "kim", "s-1", "기준금리 전망은?", "2.5%로 예상됩니다.", 60),
	make_log(2, "kim", "s-1", "그 이유는?", "물가가 안정돼서입니다.", 59),
	make_log(3, "kim", "s-2", "환율은?", "1,390원입니다.", 1),
	make_log(4, "lee", "s-9", "남의 질문", "남의 답", 0),
]


def fake_select_logs(user_id, session_id=None):
	"""진짜 select_logs처럼 user_id(필수)·session_id(선택)로 찾고 최신순으로 돌려줌"""
	found = []
	for log in FAKE_DB:
		if log.user_id != user_id:
			continue
		if session_id != None and log.session_id != session_id:
			continue
		found.append(log)
	found.sort(key=lambda log: log.created_at, reverse=True)
	return found


history_service.select_logs = fake_select_logs


# ----------------------------------------------------------------
# 2. 가짜 질의응답 그래프 (노드 여러 개 + InMemorySaver)
# ----------------------------------------------------------------
class QAState(TypedDict, total=False):
	messages: Annotated[list, add_messages]
	question: str


def generate(state):
	count = len(state.get("messages", []))
	return {"messages": [AIMessage(content=f"앞 대화 {count}개를 기억하고 답합니다")]}


graph_box = {"graph": None}


def fake_get_qa_graph():
	if graph_box["graph"] == None:
		builder = StateGraph(QAState)
		builder.add_node("validate", lambda state: {})
		builder.add_node("generate", generate)
		builder.add_edge(START, "validate")
		builder.add_edge("validate", "generate")
		builder.add_edge("generate", END)
		graph_box["graph"] = builder.compile(checkpointer=InMemorySaver())
	return graph_box["graph"]


registry = types.ModuleType("hri.graph.registry")
registry.get_qa_graph = fake_get_qa_graph
sys.modules["hri.graph.registry"] = registry


# ----------------------------------------------------------------
# 3. 테스트
# ----------------------------------------------------------------
from hri.service.history_service import get_history, restore_session  # noqa: E402

passed = 0
total = 0


def check(name, ok, info=""):
	global passed, total
	total = total + 1
	if ok:
		passed = passed + 1
		print(f"✅ {name} {info}")
	else:
		print(f"❌ {name} {info}")


config = {"configurable": {"thread_id": "s-1"}}

# get_history
history = get_history("kim")
check("1 get_history(kim) → 3개 최신순", [log.question for log in history] == ["환율은?", "그 이유는?", "기준금리 전망은?"], [log.question for log in history])
check("2 get_history(nobody) → []", get_history("nobody") == [])

# restore_session
chat = restore_session("s-1", "kim")
check("3 restore_session(s-1) → 오래된 순 Chatbot 형식", chat == [
	{"role": "user", "content": "기준금리 전망은?"},
	{"role": "assistant", "content": "2.5%로 예상됩니다."},
	{"role": "user", "content": "그 이유는?"},
	{"role": "assistant", "content": "물가가 안정돼서입니다."},
])
for item in chat:
	print("     ", item)

graph = fake_get_qa_graph()
saved = graph.get_state(config).values.get("messages", [])
check("4 체크포인터에 Human/AI 4개", [type(m).__name__ for m in saved] == ["HumanMessage", "AIMessage", "HumanMessage", "AIMessage"], [type(m).__name__ for m in saved])

restore_session("s-1", "kim")
check("5 다시 복원해도 중복 안 쌓임", len(graph.get_state(config).values["messages"]) == 4)

result = graph.invoke({"question": "더 자세히?"}, config)
check("6 이어서 질문하면 앞 대화 기억", "앞 대화 4개" in result["messages"][-1].content, result["messages"][-1].content)

graph_box["graph"] = None   # 서버 재시작 흉내 (InMemorySaver 비우기)
restore_session("s-1", "kim")
check("7 재시작 후 복원 → 다시 4개", len(fake_get_qa_graph().get_state(config).values["messages"]) == 4)

check("8 남의 대화(s-9) 복원 → []", restore_session("s-9", "kim") == [])
check("9 없는 대화 → []", restore_session("s-없음", "kim") == [])

print(f"\n통과 {passed}/{total}")