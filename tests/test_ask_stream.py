"""ask_stream 로직 테스트 · 가짜 그래프와 가짜 모델을 쓴 진짜 LangGraph 사용 (장유빈)"""
import sys
import types
from datetime import datetime

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from hri.domain.schemas import AnswerResult, QAState
from hri.service import qa_service
from hri.service.qa_service import ERROR_ANSWER, ask_stream


class _FakeGraph:
	"""stream()이 script가 만든 (모드, 내용) 조각을 차례로 내놓는 가짜 그래프. 받은 인자를 기록한다."""

	def __init__(self, script):
		self.script = script
		self.calls = []

	def stream(self, inputs, config=None, stream_mode=None):
		self.calls.append({"inputs": inputs, "config": config, "stream_mode": stream_mode})
		yield from self.script(inputs, config)


def _use_graph(monkeypatch, script) -> _FakeGraph:
	graph = _FakeGraph(script)
	monkeypatch.setattr(qa_service, "_get_graph", lambda: graph)
	return graph


def _tokens(*texts, node="generate"):
	return [("messages", (AIMessageChunk(content=t), {"langgraph_node": node})) for t in texts]


def _update(node, **values):
	return ("updates", {node: values})


def _ask(**kwargs):
	args = dict(question="금리 전망은?", persona="실무자", session_id="s1", user_id="u1")
	args.update(kwargs)
	return list(ask_stream(**args))


def _rag_script(inputs, config):
	"""근거가 있는 질문의 한 턴: 글자 조각 → generate 결과 → 출처·추천 질문 → 기록."""
	yield from _tokens("안", "녕")
	# LangGraph는 노드가 돌려준 질문·완성 답변도 같은 노드 이름으로 한 번 더 보낸다 (내보내면 안 된다)
	yield ("messages", (HumanMessage("금리 전망은?"), {"langgraph_node": "generate"}))
	yield ("messages", (AIMessage("안녕"), {"langgraph_node": "generate"}))
	yield _update("generate", answer="안녕", route="rag", has_source=True, messages=["x"])
	yield _update("sources", sources=[{"report_title": "보고서A"}])
	yield _update("suggest", suggestions=["다음 질문?"])
	yield _update("save_log", log_id=9)


# ---------------- 글자 조각·결과 ----------------
def test_rag_turn_streams_tokens_then_result(monkeypatch):
	_use_graph(monkeypatch, _rag_script)
	*chunks, result = _ask()
	assert chunks == ["안", "녕"]  # 질문·완성 답변이 섞여 나오지 않는다
	assert isinstance(result, AnswerResult)
	assert result.answer == "안녕" and result.route == "rag" and result.has_source is True
	assert result.sources == [{"report_title": "보고서A"}]
	assert result.suggestions == ["다음 질문?"]
	assert result.log_id == 9
	assert result.question == "금리 전망은?"


def test_tokens_from_other_nodes_are_ignored(monkeypatch):
	def script(inputs, config):
		yield from _tokens('{"route": "rewrite"}', node="rewrite")
		yield from _tokens("[추천]", node="suggest")
		yield from _tokens("답", node="generate")
		yield _update("generate", answer="답", route="rag", has_source=True)

	_use_graph(monkeypatch, script)
	assert _ask()[:-1] == ["답"]


def test_chat_node_tokens_are_streamed(monkeypatch):
	def script(inputs, config):
		yield from _tokens("안녕", "하세요", node="chat")
		yield _update("chat", answer="안녕하세요", route="chat", has_source=False)
		yield _update("save_log", log_id=2)

	_use_graph(monkeypatch, script)
	*chunks, result = _ask(question="안녕")
	assert chunks == ["안녕", "하세요"]
	assert result.route == "chat" and result.has_source is False and result.suggestions == []


@pytest.mark.parametrize(
	"node, answer, route",
	[("no_answer", "자료에 없습니다", "no_answer"), ("error_reply", "질문을 입력해 주세요.", "error")],
)
def test_answer_without_llm_is_sent_whole_from_updates(monkeypatch, node, answer, route):
	def script(inputs, config):
		# 노드가 돌려준 완성 답변이 messages 모드로도 오지만 글자 조각이 아니라 무시한다
		yield ("messages", (AIMessage(answer), {"langgraph_node": node}))
		yield _update(node, answer=answer, route=route, has_source=False)
		yield _update("save_log", log_id=4)

	_use_graph(monkeypatch, script)
	*chunks, result = _ask()
	assert chunks == [answer]
	assert result.answer == answer and result.route == route and result.has_source is False
	assert result.sources == []


def test_generate_without_streamed_tokens_sends_answer_once(monkeypatch):
	def script(inputs, config):
		yield _update("generate", answer="한 번에", route="rag", has_source=True)

	_use_graph(monkeypatch, script)
	assert _ask()[:-1] == ["한 번에"]


def test_streamed_answer_is_not_sent_again_from_updates(monkeypatch):
	_use_graph(monkeypatch, _rag_script)
	assert _ask()[:-1] == ["안", "녕"]  # generate의 update에 answer가 있어도 다시 내보내지 않는다


def test_result_is_the_last_item_and_only_one(monkeypatch):
	_use_graph(monkeypatch, _rag_script)
	items = _ask()
	assert isinstance(items[-1], AnswerResult)
	assert sum(isinstance(i, AnswerResult) for i in items) == 1


def test_tokens_are_counted_from_usage_handler(monkeypatch):
	def script(inputs, config):
		config["configurable"]["usage"].usage_metadata = {
			"gpt-5-mini": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
			"gpt-4o-mini": {"input_tokens": 10, "output_tokens": 5},
		}
		yield _update("generate", answer="답", route="rag", has_source=True)

	_use_graph(monkeypatch, script)
	assert _ask()[-1].tokens == 135  # 120 + (10 + 5)
	assert _ask()[-1].cost_krw == 0.0  # 원화 비용은 save_log 노드가 저장한다


def test_question_is_masked_when_graph_gives_none(monkeypatch):
	def script(inputs, config):
		yield _update("no_answer", answer="자료에 없습니다", route="no_answer", has_source=False)

	_use_graph(monkeypatch, script)
	assert _ask(question="이름: 홍길동")[-1].question == "이름: 홍*동"


def test_question_from_validate_node_is_used(monkeypatch):
	def script(inputs, config):
		yield _update("validate", question="가린 질문")
		yield _update("no_answer", answer="자료에 없습니다", route="no_answer", has_source=False)

	_use_graph(monkeypatch, script)
	assert _ask(question="원래 질문")[-1].question == "가린 질문"


# ---------------- 그래프에 넘기는 값 ----------------
def test_graph_inputs_reset_every_turn_field(monkeypatch):
	graph = _use_graph(monkeypatch, _rag_script)
	_ask(question="금리는?", persona="전문가", report_ids=[3, 4], k=6, use_mmr=True)
	inputs = graph.calls[0]["inputs"]
	assert inputs["question"] == "금리는?" and inputs["persona"] == "전문가"
	assert inputs["report_ids"] == [3, 4] and inputs["k"] == 6 and inputs["use_mmr"] is True
	assert inputs["user_id"] == "u1" and inputs["error"] is None and inputs["answer"] == ""
	for key in ("docs", "context_docs", "sources", "suggestions", "terms"):
		assert inputs[key] == []
	assert inputs["started_at"] is not None
	assert "messages" not in inputs  # 이전 대화는 체크포인터가 이어 준다


def test_missing_report_ids_become_empty_list(monkeypatch):
	graph = _use_graph(monkeypatch, _rag_script)
	_ask(report_ids=None)
	assert graph.calls[0]["inputs"]["report_ids"] == []


def test_config_has_thread_id_and_shared_usage_handler(monkeypatch):
	graph = _use_graph(monkeypatch, _rag_script)
	_ask(session_id="abc")
	config = graph.calls[0]["config"]
	handler = config["configurable"]["usage"]
	assert config["configurable"]["thread_id"] == "abc"
	assert config["callbacks"] == [handler]  # 같은 핸들러를 save_log 노드와 ask_stream이 함께 쓴다
	assert graph.calls[0]["stream_mode"] == ["messages", "updates"]


# ---------------- 예외 ----------------
def _boom_script(inputs, config):
	yield from _tokens("절반")
	raise RuntimeError("그래프 밖 실패")


def test_exception_gives_error_result_and_records_it(monkeypatch):
	saved = {}

	def fake_save(question, persona, session_id, user_id, started_at, error):
		saved.update(question=question, persona=persona, session_id=session_id, user_id=user_id, error=str(error))
		return 77

	monkeypatch.setattr(qa_service, "_save_error_log", fake_save)
	_use_graph(monkeypatch, _boom_script)
	*chunks, result = _ask(question="금리는?")
	assert chunks == ["절반"]
	assert result.route == "error" and result.answer == ERROR_ANSWER
	assert result.has_source is False and result.log_id == 77
	assert saved == {"question": "금리는?", "persona": "실무자", "session_id": "s1", "user_id": "u1", "error": "그래프 밖 실패"}


def test_exception_after_graph_already_logged_is_not_saved_twice(monkeypatch):
	def script(inputs, config):
		yield _update("save_log", log_id=3)
		raise RuntimeError("실패")

	calls = []
	monkeypatch.setattr(qa_service, "_save_error_log", lambda *args: calls.append(args) or 99)
	_use_graph(monkeypatch, script)
	assert _ask()[-1].log_id == 3
	assert calls == []


def _fake_history_module(monkeypatch, save):
	module = types.ModuleType("hri.service.history_service")
	module.save_qa_log = save
	monkeypatch.setitem(sys.modules, "hri.service.history_service", module)


def test_save_error_log_builds_error_qalog(monkeypatch):
	logs = []
	_fake_history_module(monkeypatch, lambda log: logs.append(log) or 5)
	log_id = qa_service._save_error_log("이름: 홍길동", "없는 페르소나", "s1", "u1", datetime.now(), ValueError("x" * 600))
	assert log_id == 5
	[log] = logs
	assert log.route == "error" and log.has_source is False and log.answer == ERROR_ANSWER
	assert log.question == "이름: 홍*동"  # 개인정보는 가려서 저장
	assert log.persona == "실무자"  # 알 수 없는 페르소나는 실무자
	assert len(log.error) == 500 and log.tokens == 0 and log.cost_krw == 0.0


def test_save_error_log_failure_returns_none(monkeypatch):
	def broken(log):
		raise RuntimeError("DB 실패")

	_fake_history_module(monkeypatch, broken)
	assert qa_service._save_error_log("q", "실무자", "s1", "u1", datetime.now(), ValueError("x")) is None


# ---------------- 진짜 LangGraph ----------------
def _real_graph(model):
	"""generate 노드 하나짜리 진짜 그래프. 노드가 LLM을 invoke하고 [질문, 답변]을 돌려주는 모양은 qa_graph와 같다."""

	def generate(state):
		reply = model.invoke(state["question"])
		return {
			"answer": reply.content, "route": "rag", "has_source": True,
			"messages": [HumanMessage(state["question"]), AIMessage(reply.content)],
		}

	graph = StateGraph(QAState)
	graph.add_node("generate", generate)
	graph.add_edge(START, "generate")
	graph.add_edge("generate", END)
	return graph.compile(checkpointer=InMemorySaver())


def test_real_langgraph_streams_each_token_once(monkeypatch):
	graph = _real_graph(FakeListChatModel(responses=["안녕하세요"]))
	monkeypatch.setattr(qa_service, "_get_graph", lambda: graph)
	*chunks, result = _ask(question="질문")
	assert "".join(chunks) == "안녕하세요"  # 질문이나 완성 답변이 끼어들지 않고 정확히 한 번
	assert len(chunks) > 1  # 글자 조각으로 나뉘어 온다
	assert result.answer == "안녕하세요" and result.route == "rag"


def test_real_langgraph_keeps_conversation_between_turns(monkeypatch):
	graph = _real_graph(FakeListChatModel(responses=["첫 답", "둘째 답"]))
	monkeypatch.setattr(qa_service, "_get_graph", lambda: graph)
	_ask(question="첫 질문", session_id="same")
	_ask(question="둘째 질문", session_id="same")
	messages = graph.get_state({"configurable": {"thread_id": "same"}}).values["messages"]
	assert [m.content for m in messages] == ["첫 질문", "첫 답", "둘째 질문", "둘째 답"]