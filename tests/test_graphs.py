"""그래프 분기·통합 테스트 · test_*_graph (엄정현)

[3.8.8] 4개 그래프가 명세대로 연결되어 compile되는지, registry가 한 번만 만드는지 확인한다.
[3.8.4] 질의응답 그래프의 분기(route_question·route_after_grade)와 체크포인터를 확인한다.
[3.8.1] 업로드 그래프의 검증 노드(validate_node)·검증 분기(route_after_validate)를 진짜 validate_pdf()와 임시 PDF로 확인한다.
[3.8.5] 질의응답의 답변 노드(generate·no_answer·error_reply·chat)와 기록 노드(save_log)를 가짜 함수로 확인한다.
[3.8.6] 요약 그래프의 캐시·섹션 분배(fan_out_sections)·3줄·저장 흐름을 가짜 report_dao·summary_prompt 모듈로 확인한다.
노드는 아직 가짜({} 반환)라서 입력으로 넣은 값(error·docs·sections 등)으로 분기를 확인한다 (네트워크·DB·LLM 없이 실행).
route_question이 부르는 get_chat_model은 가짜 모듈로 바꿔 끼운다 (진짜 llm_client는 import할 때 .env가 필요하고 OpenAI를 부름).
FakeToolChatModel을 쓰는 노드 동작 테스트와 ingest 통합 테스트는 3.8.9에서 이 파일에 추가한다.

실행: poetry run python -m pytest tests/test_graphs.py -v
"""

import importlib
import subprocess
import sys
import types
from datetime import datetime, timedelta

import pymupdf
import pytest
from langgraph.graph.state import CompiledStateGraph

from langgraph.checkpoint.memory import InMemorySaver

from langchain_core.messages import AIMessage, HumanMessage

from hri.domain.schemas import ContextDoc, LlmUsage, QALog, ReportSummary, RouteDecision, Section, SectionSummary, Settings, ThreeLineSummary, ValidationResult
from hri.graph import registry
from hri.graph.ingest_graph import PARALLEL_EXTRACT, VALIDATE_FAILED_REASON, route_after_validate, validate_node
from hri.graph.checkpointer import get_checkpointer
from hri.graph.summary_graph import NO_SECTIONS_ERROR, fan_out_sections, sort_section_summaries
from hri.graph.qa_graph import (
	GREETING_RE,
	NO_ANSWER_TEXT,
	ROUTE_SYSTEM_PROMPT,
	chat_node,
	error_reply_node,
	generate_node,
	no_answer_node,
	route_after_grade,
	route_question,
	save_log_node,
)
from hri.graph.visualize import draw_graphs
from tests.fakes import FakeExtractors, FakeIngestStore, install_extract_stubs, install_ingest_store

GETTERS = [registry.get_ingest_graph, registry.get_qa_graph, registry.get_summary_graph, registry.get_agent_graph]


# 테스트마다 그래프·체크포인터를 새로 만들게 캐시를 비운다 (앞 테스트의 대화 기록이 남지 않게)
@pytest.fixture(autouse=True)
def clear_graph_cache():
	for getter in GETTERS:
		getter.cache_clear()
	get_checkpointer.cache_clear()
	yield
	for getter in GETTERS:
		getter.cache_clear()
	get_checkpointer.cache_clear()


class FakeClassifier:
	"""get_chat_model()이 돌려주는 모델 흉내 (진짜 llm_client 대신 끼우는 가짜 모듈의 내용).

	- 질문 분류: with_structured_output(RouteDecision).invoke()가 route(정해 둔 값)를 돌려준다
	- 답변 생성: invoke(messages)가 AIMessage(answer)를 돌려준다 (generate 노드)
	- 잡담: chat_bot(question)이 chat_answer를 돌려준다 (chat 노드)
	- 비용: cost_korean_won(usage, usd_to_krw)·get_usd_krw()는 호출을 기록하고 정해 둔 값을 돌려준다
	기록: calls(get_chat_model 인자) · schemas(구조화 양식) · inputs(분류 입력) · answer_inputs(답변 입력) ·
	      chat_inputs · cost_calls · usd_calls
	"""

	def __init__(self, route="chat"):
		self.route = route
		self.answer = "LLM 답변"
		self.chat_answer = "안녕하세요! 무엇을 도와드릴까요?"
		self.usd_krw = 1400.0
		self.calls, self.schemas, self.inputs = [], [], []
		self.answer_inputs, self.chat_inputs, self.cost_calls, self.usd_calls = [], [], [], []

	def get_chat_model(self, **kwargs):
		self.calls.append(kwargs)
		return self

	def with_structured_output(self, schema):
		self.schemas.append(schema)
		fake = self

		class _Structured:
			def invoke(self, messages):
				fake.inputs.append(messages)
				return schema(route=fake.route)

		return _Structured()

	def invoke(self, messages):
		self.answer_inputs.append(messages)
		return AIMessage(self.answer)

	def chat_bot(self, question):
		self.chat_inputs.append(question)
		return self.chat_answer

	def cost_korean_won(self, usage, usd_to_krw):
		self.cost_calls.append((usage, usd_to_krw))
		return 12.5

	def get_usd_krw(self):
		self.usd_calls.append(True)
		return self.usd_krw


# 진짜 llm_client 대신 가짜 모듈을 끼운다. search 모듈도 가짜 llm_client로 다시 import되게 지운다 (extract_keywords는 진짜 사용)
@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
	fake = FakeClassifier()
	module = types.ModuleType("hri.llm.llm_client")
	for name in ("get_chat_model", "chat_bot", "cost_korean_won", "get_usd_krw"):
		setattr(module, name, getattr(fake, name))
	monkeypatch.setitem(sys.modules, "hri.llm.llm_client", module)
	monkeypatch.delitem(sys.modules, "hri.retriever.search", raising=False)
	return fake


class FakeQaDeps:
	"""질의응답 노드가 부르는 다른 팀원 함수의 가짜 (아직 레포에 없거나 DB가 필요한 함수).

	- get_context_docs(chunks)   : 김희진 report_dao (3.2.16)  → 청크마다 ContextDoc 1개
	- build_messages(...)        : 장유빈 rag_prompt (3.3.5)   → 받은 인자를 그대로 담은 메시지 목록
	- save_qa_log(QALog)         : 임세준 history_service (3.6.2) → log_id 101부터
	- insert_usage(LlmUsage)     : 임세준 usage_dao (3.6.12)
	format_docs·has_context(장유빈 rag_prompt)는 레포에 있는 진짜 함수를 쓴다.
	"""

	def __init__(self):
		self.context_calls, self.build_calls, self.logs, self.usages = [], [], [], []

	def get_context_docs(self, chunks):
		self.context_calls.append(chunks)
		return [ContextDoc(report_id=1, report_title="2026년 경제전망", kind="text", section_title="1. 개요",
			page_start=1, page_end=2, text=f"근거 {i}") for i, _ in enumerate(chunks)]

	def build_messages(self, question, context, persona, history):
		self.build_calls.append({"question": question, "context": context, "persona": persona, "history": list(history)})
		return [{"role": "user", "content": f"{question}\n[자료]\n{context}"}]

	def save_qa_log(self, log):
		self.logs.append(log)
		return 100 + len(self.logs)

	def insert_usage(self, usage):
		self.usages.append(usage)
		return len(self.usages)


@pytest.fixture(autouse=True)
def qa_deps(monkeypatch):
	import hri.dao.report_dao as report_dao
	import hri.dao.usage_dao as usage_dao
	import hri.prompt.rag_prompt as rag_prompt
	import hri.service.history_service as history_service

	fake = FakeQaDeps()
	monkeypatch.setattr(report_dao, "get_context_docs", fake.get_context_docs, raising=False)
	monkeypatch.setattr(rag_prompt, "build_messages", fake.build_messages, raising=False)
	monkeypatch.setattr(history_service, "save_qa_log", fake.save_qa_log, raising=False)
	monkeypatch.setattr(usage_dao, "insert_usage", fake.insert_usage, raising=False)
	return fake


# ask_stream()이 매 턴 넣어 주는 입력(명세서 ask_stream 행의 초기화 목록)을 흉내 낸다
def qa_input(**overrides):
	inputs = {
		"question": "금리 전망은?", "persona": "실무자", "report_ids": [1], "k": 4, "use_mmr": False, "user_id": "tester",
		"error": None, "docs": [], "context_docs": [], "sources": [], "suggestions": [], "terms": [], "answer": "",
		"started_at": datetime.now(),
	}
	inputs.update(overrides)
	return inputs


# 그래프를 실행하며 실행된 노드 이름을 순서대로 모은다 (stream_mode='updates': 노드가 끝날 때마다 {노드: 바뀐 값})
def run_nodes(graph, inputs, config=None):
	nodes = []
	for update in graph.stream(inputs, config=config, stream_mode="updates"):
		nodes.extend(update.keys())
	return nodes


# 그래프 구조에서 (출발 노드, 도착 노드) 쌍을 모은다
def edges_of(graph):
	return {(e.source, e.target) for e in graph.get_graph().edges}


def nodes_of(graph):
	return set(graph.get_graph().nodes) - {"__start__", "__end__"}


# ─────────────────────────────────────────────────────────────
# 1. registry: 지연 싱글턴
# ─────────────────────────────────────────────────────────────

# [확인 1] registry를 import해도 그래프 파일은 아직 import되지 않아야 한다 (build import는 함수 안 → 순환 import 방지)
def test_registry_import_does_not_import_graph_modules():
	code = (
		"import sys, hri.graph.registry; "
		"mods = ['hri.graph.ingest_graph', 'hri.graph.qa_graph', 'hri.graph.summary_graph', 'hri.graph.agent_graph']; "
		"print([m for m in mods if m in sys.modules])"
	)
	out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
	assert out.stdout.strip() == "[]"


# [확인 2] get_*_graph()는 compile된 그래프를 돌려주고, 두 번 불러도 같은 객체여야 한다 (lru_cache)
@pytest.mark.parametrize("getter", GETTERS)
def test_get_graph_returns_same_compiled_graph(getter):
	first = getter()
	assert isinstance(first, CompiledStateGraph)
	assert getter() is first


# [확인 3] cache_clear() 뒤에는 새로 만들어야 한다 (테스트에서 가짜 모델로 바꿀 때 사용)
@pytest.mark.parametrize("getter", GETTERS)
def test_cache_clear_rebuilds_graph(getter):
	first = getter()
	getter.cache_clear()
	assert getter() is not first


# [확인 4] 체크포인터는 qa 그래프에만 붙고, get_checkpointer()의 하나뿐인 객체여야 한다
def test_only_qa_graph_has_checkpointer():
	assert registry.get_qa_graph().checkpointer is get_checkpointer()
	assert get_checkpointer() is get_checkpointer()
	for getter in (registry.get_ingest_graph, registry.get_summary_graph, registry.get_agent_graph):
		assert getter().checkpointer is None


# ─────────────────────────────────────────────────────────────
# 2. 노드·엣지가 명세서(LangGraph 워크플로우 명세서 build_*_graph 행)와 같은지
# ─────────────────────────────────────────────────────────────

# [확인 5] 그래프별 노드 이름
@pytest.mark.parametrize(
	("getter", "names"),
	[
		(registry.get_ingest_graph, {
			"validate", "load_text", "extract_tables", "extract_charts", "process_charts",
			"split", "summarize_tables", "persist",
		}),
		(registry.get_qa_graph, {
			"validate", "chat", "error_reply", "rewrite", "retrieve", "grade",
			"no_answer", "generate", "sources", "suggest", "save_log",
		}),
		(registry.get_summary_graph, {"load_cache", "load_sections", "summarize_section", "three_lines", "save"}),
		(registry.get_agent_graph, {"agent", "tools"}),
	],
)
def test_graph_nodes_match_spec(getter, names):
	assert nodes_of(getter()) == names


# [확인 6] 업로드 그래프: 검증 → 추출 3개 병렬 → 차트는 process_charts → 세 갈래가 split으로 모임 → 저장
def test_ingest_edges():
	edges = edges_of(registry.get_ingest_graph())
	assert {
		("__start__", "validate"),
		("validate", "load_text"), ("validate", "extract_tables"), ("validate", "extract_charts"), ("validate", "__end__"),
		("extract_charts", "process_charts"),
		("load_text", "split"), ("extract_tables", "split"), ("process_charts", "split"),
		("split", "summarize_tables"), ("summarize_tables", "persist"), ("persist", "__end__"),
	} == edges


# [확인 7] 질의응답 그래프: 분류 3갈래 → 근거 분기 → [sources ∥ suggest] → save_log
def test_qa_edges():
	edges = edges_of(registry.get_qa_graph())
	assert {
		("__start__", "validate"),
		("validate", "chat"), ("validate", "rewrite"), ("validate", "error_reply"),
		("rewrite", "retrieve"), ("retrieve", "grade"),
		("grade", "generate"), ("grade", "no_answer"),
		("generate", "sources"), ("generate", "suggest"),
		("sources", "save_log"), ("suggest", "save_log"),
		("chat", "save_log"), ("no_answer", "save_log"), ("error_reply", "save_log"),
		("save_log", "__end__"),
	} == edges


# [확인 8] 요약 그래프: 캐시 → 섹션 → 섹션별 요약 → 3줄 → 저장
def test_summary_edges():
	edges = edges_of(registry.get_summary_graph())
	assert {
		("__start__", "load_cache"),
		("load_cache", "load_sections"), ("load_cache", "__end__"),
		("load_sections", "summarize_section"), ("load_sections", "__end__"),
		("summarize_section", "three_lines"), ("three_lines", "save"), ("save", "__end__"),
	} == edges


# [확인 9] 에이전트 그래프: agent ↔ tools 반복, 도구 호출이 없으면 끝
def test_agent_edges():
	edges = edges_of(registry.get_agent_graph())
	assert {("__start__", "agent"), ("agent", "tools"), ("agent", "__end__"), ("tools", "agent")} == edges


# ─────────────────────────────────────────────────────────────
# 3. 가짜 노드로 실제 실행해 분기 확인
# ─────────────────────────────────────────────────────────────

# validate 노드가 진짜 validate_pdf()를 부르도록 준비한다 (네트워크·DB·.env 없이).
#   - document_service는 cost_korean_won(hri.llm.llm_client)·insert_usage(hri.dao.usage_dao)도 import하므로
#     호출 기록 없는 가짜로 바꿔 끼운다 (업로드 그래프 검증에는 쓰지 않음)
#     hri.llm.llm_client는 fake_llm fixture(autouse)가 이미 가짜 모듈로 바꿔 두었으므로 cost_korean_won만 붙인다
#   - load_settings()는 .env의 MySQL 값이 없으면 오류이므로 가짜 Settings(MAX_MB=30, MAX_PAGES=150 기본값)로 바꾼다
@pytest.fixture
def document_service(monkeypatch, fake_llm):
	monkeypatch.setattr(sys.modules["hri.llm.llm_client"], "cost_korean_won", lambda *a, **k: 0.0, raising=False)
	usage_dao = types.ModuleType("hri.dao.usage_dao")
	usage_dao.insert_usage = lambda *a, **k: None
	monkeypatch.setitem(sys.modules, "hri.dao.usage_dao", usage_dao)
	monkeypatch.delitem(sys.modules, "hri.service.document_service", raising=False)

	module = importlib.import_module("hri.service.document_service")
	settings = Settings(mysql_host="localhost", mysql_user="test", mysql_password="test", mysql_database="test")
	monkeypatch.setattr(module, "load_settings", lambda: settings)
	return module


# 빈 쪽 page_count개짜리 PDF를 tmp_path에 만든다 (password를 주면 여는 암호를 건다)
def make_pdf(path, page_count=1, password=None):
	doc = pymupdf.open()
	for _ in range(page_count):
		doc.new_page(width=595, height=842)
	if password is None:
		doc.save(str(path))
	else:
		doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password)
	doc.close()
	return str(path)


# [확인 10] 업로드 정상: 진짜 PDF면 validate가 {'error': None}을 내고, 8개 노드가 모두 실행되며 split은 세 갈래가 끝난 뒤 한 번만 실행
def test_ingest_runs_all_nodes(document_service, tmp_path, monkeypatch):
	# split·persist 노드(3.8.3)가 부르는 설정·DB·Chroma와, 추출 노드(3.8.2)가 부르는 아직 없는 함수는 가짜로 바꿔 끼운다
	install_ingest_store(monkeypatch, FakeIngestStore())
	install_extract_stubs(monkeypatch, FakeExtractors())
	path = make_pdf(tmp_path / "report.pdf", page_count=3)
	inputs = {"file_path": path, "user_id": "tester"}
	updates = list(registry.get_ingest_graph().stream(inputs, stream_mode="updates"))
	nodes = [name for update in updates for name in update]

	assert updates[0] == {"validate": {"error": None}}
	assert set(nodes[1:4]) == set(PARALLEL_EXTRACT)
	assert nodes[4:] == ["process_charts", "split", "summarize_tables", "persist"]


# [확인 11] 업로드 검증 실패: validate_pdf()의 reason이 state['error']에 들어가고, validate 다음 바로 끝 (추출 노드 실행 안 함)
@pytest.mark.parametrize("kind", ["not_pdf", "missing", "encrypted"])
def test_ingest_stops_when_validate_fails(document_service, tmp_path, kind):
	if kind == "not_pdf":
		path = tmp_path / "memo.txt"
		path.write_text("PDF가 아님", encoding="utf-8")
		path = str(path)
	elif kind == "missing":
		path = str(tmp_path / "없는파일.pdf")
	else:
		path = make_pdf(tmp_path / "locked.pdf", password="1234")

	expected = document_service.validate_pdf(path)
	assert expected.ok is False

	graph = registry.get_ingest_graph()
	nodes = run_nodes(graph, {"file_path": path, "user_id": "tester"})
	assert nodes == ["validate"]
	assert graph.invoke({"file_path": path, "user_id": "tester"})["error"] == expected.reason


# [확인 29] 검증 노드·분기 단위 확인
#   - route_after_validate: error가 있으면 END, 없으면(None·키 없음) 추출 노드 3개 리스트
#   - validate_node: ok=False인데 reason이 비어 있으면 VALIDATE_FAILED_REASON, ok=True면 {'error': None}
def test_validate_node_and_route(document_service, monkeypatch):
	assert route_after_validate({"error": "PDF 파일만 업로드할 수 있습니다"}) == "__end__"
	assert route_after_validate({"error": None}) == ["load_text", "extract_tables", "extract_charts"]
	assert route_after_validate({}) == ["load_text", "extract_tables", "extract_charts"]

	monkeypatch.setattr(document_service, "validate_pdf", lambda path: ValidationResult(ok=False))
	assert validate_node({"file_path": "x.pdf"}) == {"error": VALIDATE_FAILED_REASON}

	monkeypatch.setattr(document_service, "validate_pdf", lambda path: ValidationResult(ok=True))
	assert validate_node({"file_path": "x.pdf"}) == {"error": None}


# [확인 12] 질의응답 3갈래 + 근거 분기가 save_log로 끝나야 한다
@pytest.mark.parametrize(
	("inputs", "expected"),
	[
		(qa_input(docs=["근거 청크"]),
		 ["validate", "rewrite", "retrieve", "grade", "generate", {"sources", "suggest"}, "save_log"]),
		(qa_input(docs=[]),
		 ["validate", "rewrite", "retrieve", "grade", "no_answer", "save_log"]),
		(qa_input(question="", error="질문을 입력해 주세요"),
		 ["validate", "error_reply", "save_log"]),
	],
	ids=["근거_있음", "근거_없음", "입력_오류"],
)
def test_qa_routes(inputs, expected):
	config = {"configurable": {"thread_id": "test-session"}}
	nodes = run_nodes(registry.get_qa_graph(), inputs, config)
	# {"sources", "suggest"}처럼 집합으로 적은 칸은 같은 단계에서 병렬 실행되어 순서가 정해지지 않은 노드들
	flat = []
	for step in expected:
		if isinstance(step, set):
			assert set(nodes[len(flat):len(flat) + len(step)]) == step
			flat.extend(nodes[len(flat):len(flat) + len(step)])
		else:
			flat.append(step)
	assert nodes == flat


# [확인 13] 질의응답 그래프는 thread_id가 없으면 실행되지 않아야 한다 (체크포인터가 붙어 있음)
def test_qa_requires_thread_id():
	with pytest.raises(ValueError):
		registry.get_qa_graph().invoke({"question": "q"})


# 요약 그래프 노드가 부르는 김희진 담당 함수를 호출 기록을 남기는 가짜로 바꿔 끼운다 (아직 레포에 없음, DB·LLM 없이 실행)
#   hri.dao.report_dao: select_sections·update_summary_json·select_summary_json(캐시 읽기, 이름 확인 필요)
#   hri.prompt.summary_prompt: summarize_sections·summarize_three_lines
class FakeSummaryDeps:
	def __init__(self):
		self.sections = []        # select_sections가 돌려줄 섹션
		self.cache = None         # select_summary_json이 돌려줄 캐시 (None = 없음)
		self.calls = {"select_sections": [], "summarize_sections": [], "summarize_three_lines": [], "update_summary_json": [], "select_summary_json": []}

	def select_summary_json(self, report_id):
		self.calls["select_summary_json"].append(report_id)
		return self.cache

	def select_sections(self, report_id):
		self.calls["select_sections"].append(report_id)
		return self.sections

	def summarize_sections(self, sections):
		self.calls["summarize_sections"].append(sections)
		return [SectionSummary(title=s.title, summary=f"{s.title} 요약", start_page=s.start_page, end_page=s.end_page) for s in sections]

	def summarize_three_lines(self, section_summaries):
		self.calls["summarize_three_lines"].append(section_summaries)
		return ThreeLineSummary(reasoning="생각", lines=["주장", "근거", "시사점"])

	def update_summary_json(self, report_id, summary):
		self.calls["update_summary_json"].append((report_id, summary))
		return 1


@pytest.fixture
def summary_deps(monkeypatch):
	fake = FakeSummaryDeps()
	report_dao = types.ModuleType("hri.dao.report_dao")
	for name in ("select_summary_json", "select_sections", "update_summary_json"):
		setattr(report_dao, name, getattr(fake, name))
	summary_prompt = types.ModuleType("hri.prompt.summary_prompt")
	for name in ("summarize_sections", "summarize_three_lines"):
		setattr(summary_prompt, name, getattr(fake, name))
	monkeypatch.setitem(sys.modules, "hri.dao.report_dao", report_dao)
	monkeypatch.setitem(sys.modules, "hri.prompt.summary_prompt", summary_prompt)
	import hri.dao, hri.prompt
	monkeypatch.setattr(hri.dao, "report_dao", report_dao, raising=False)
	monkeypatch.setattr(hri.prompt, "summary_prompt", summary_prompt, raising=False)
	return fake


def make_section(no, title, start, end):
	return Section(report_id=1, section_no=no, title=title, start_page=start, end_page=end, text=f"{title} 본문")


# [확인 14] 요약 정상: 캐시 없음 → 섹션 수만큼 summarize_section(섹션 1개씩) → three_lines → save
#           three_lines·save에는 start_page 순으로 정렬된 요약이 넘어가고, 저장되는 ReportSummary에 3줄이 담긴다
def test_summary_fans_out_per_section(summary_deps):
	# 일부러 쪽 순서를 섞어 넣는다 (병렬 결과가 모이는 순서를 믿지 않는지 확인)
	summary_deps.sections = [make_section(2, "결론", 9, 10), make_section(0, "서론", 1, 3), make_section(1, "본론", 4, 8)]
	graph = registry.get_summary_graph()
	nodes = run_nodes(graph, {"report_id": 1})

	assert nodes == ["load_cache", "load_sections", "summarize_section", "summarize_section", "summarize_section", "three_lines", "save"]
	assert all(len(arg) == 1 for arg in summary_deps.calls["summarize_sections"])          # summarize_sections([section])
	assert [s.title for s in summary_deps.calls["summarize_three_lines"][0]] == ["서론", "본론", "결론"]
	report_id, saved = summary_deps.calls["update_summary_json"][0]
	assert report_id == 1 and isinstance(saved, ReportSummary)
	assert saved.three_lines == ["주장", "근거", "시사점"]
	assert [s.title for s in saved.sections] == ["서론", "본론", "결론"]


# [확인 15] 요약 섹션 0개: load_sections가 state['error']를 설정하고 바로 끝 (LLM·저장 호출 없음)
def test_summary_stops_on_error(summary_deps):
	summary_deps.sections = []
	graph = registry.get_summary_graph()
	nodes = run_nodes(graph, {"report_id": 1})
	assert nodes == ["load_cache", "load_sections"]
	assert graph.invoke({"report_id": 1})["error"] == NO_SECTIONS_ERROR
	assert summary_deps.calls["summarize_sections"] == [] and summary_deps.calls["update_summary_json"] == []


# [확인 30] 요약 캐시 있음: load_cache 다음 바로 끝, 섹션 조회·LLM·저장을 하지 않고 캐시 내용이 state에 담긴다
@pytest.mark.parametrize("as_dict", [False, True], ids=["ReportSummary", "JSON_dict"])
def test_summary_uses_cache(summary_deps, as_dict):
	cached = ReportSummary(
		report_id=1, three_lines=["캐시1", "캐시2", "캐시3"],
		sections=[SectionSummary(title="서론", summary="요약", start_page=1, end_page=3)],
	)
	summary_deps.cache = cached.model_dump(mode="json") if as_dict else cached
	graph = registry.get_summary_graph()

	assert run_nodes(graph, {"report_id": 1}) == ["load_cache"]
	result = graph.invoke({"report_id": 1})
	assert result["three_lines"] == ["캐시1", "캐시2", "캐시3"]
	assert [s.title for s in result["section_summaries"]] == ["서론"]
	assert summary_deps.calls["select_sections"] == [] and summary_deps.calls["summarize_sections"] == []
	assert summary_deps.calls["update_summary_json"] == []


# [확인 31] 캐시 읽는 DAO 함수가 아직 없으면(이름 확인 필요) 캐시가 없는 것으로 보고 새로 요약한다
def test_summary_without_cache_reader(summary_deps):
	delattr(sys.modules["hri.dao.report_dao"], "select_summary_json")
	summary_deps.sections = [make_section(0, "서론", 1, 3)]
	nodes = run_nodes(registry.get_summary_graph(), {"report_id": 1})
	assert nodes == ["load_cache", "load_sections", "summarize_section", "three_lines", "save"]


# [확인 32] fan_out_sections 단위: error면 END, 아니면 섹션마다 Send('summarize_section', {'section': s})
def test_fan_out_sections_unit():
	assert fan_out_sections({"error": NO_SECTIONS_ERROR, "sections": []}) == "__end__"
	sections = [make_section(0, "서론", 1, 3), make_section(1, "본론", 4, 8)]
	sends = fan_out_sections({"sections": sections})
	assert [(send.node, send.arg) for send in sends] == [
		("summarize_section", {"section": sections[0]}),
		("summarize_section", {"section": sections[1]}),
	]


# [확인 33] sort_section_summaries: start_page 순, start_page가 없으면 맨 뒤
def test_sort_section_summaries():
	items = [
		SectionSummary(title="C", summary="s", start_page=9, end_page=10),
		SectionSummary(title="없음", summary="s"),
		SectionSummary(title="A", summary="s", start_page=1, end_page=3),
	]
	assert [s.title for s in sort_section_summaries(items)] == ["A", "C", "없음"]


# [확인 16·17] 에이전트 실행(도구 호출 없음·recursion_limit) 테스트는 3.8.7에서 tests/test_agent_graph.py로 옮겼다
#               (agent_node가 진짜 LLM 호출 구조가 되어 FakeToolChatModel로 확인)


# ─────────────────────────────────────────────────────────────
# 4. 구조도 문서화
# ─────────────────────────────────────────────────────────────

# [확인 18] draw_graphs()는 4개 그래프를 <out_dir>/<이름>.md에 Mermaid로 저장하고 경로를 돌려준다
def test_draw_graphs_writes_mermaid(tmp_path):
	paths = draw_graphs(out_dir=str(tmp_path))
	assert [p.replace("\\", "/").rsplit("/", 1)[-1] for p in paths] == ["ingest.md", "qa.md", "summary.md", "agent.md"]
	for path in paths:
		text = open(path, encoding="utf-8").read()
		assert "```mermaid" in text and "graph TD" in text
	assert "process_charts" in open(paths[0], encoding="utf-8").read()


# ─────────────────────────────────────────────────────────────
# 5. [3.8.4] 질의응답 분기 · 체크포인터
# ─────────────────────────────────────────────────────────────

# [확인 19] GREETING_RE: 질문 '전체'가 인사·감사·잡담 표현일 때만 맞아야 한다
@pytest.mark.parametrize("text", [
	# ① 인사·감사
	"안녕하세요", "안녕", "감사합니다 ㅎㅎ", "고마워요!", "수고하셨습니다~", "안녕하세요 감사합니다", "Hello", "thanks!",
	# ② 웃음·감탄
	"ㅋㅋ", "ㅎㅎㅎ", "음...", "오", "와!", "오오~",
	# ③ 챗봇에 대한 질문
	"너는 누구야?", "넌 누구니", "누구세요?", "뭐 할 수 있어?", "너는 무엇을 할 수 있나요?", "이 서비스 기능", "이 서비스는 무슨 기능이 있어?",
	# ④ 일상 잡담
	"날씨", "오늘 날씨 어때?", "날씨 좋네요", "배고프다", "배고파요", "심심해", "잘 지내?", "잘 지내셨어요?",
	# 여러 개가 이어진 경우
	"안녕하세요! 너는 누구야?", "ㅋㅋ 심심해",
])
def test_greeting_re_matches(text):
	assert GREETING_RE.match(text)


# 문장 일부에만 잡담 표현이 있거나, 보고서 질문이 섞이면 맞지 않아야 한다 (3단계로 감)
@pytest.mark.parametrize("text", [
	"안녕하세요, 금리 전망 알려주세요", "감사합니다. 그럼 물가는요?", "GDP 성장률은?",
	"날씨가 경제에 미치는 영향은?", "오늘 환율 어때?", "음, 금리 전망은?", "ㅋㅋ 그래서 GDP는?",
	"한국은행 총재는 누구야?", "이 서비스 기능으로 지표 비교가 되나요?", "뭐 할 수 있어? 금리 전망 알려줘",
	"ㅇㅇ", "...", "",
])
def test_greeting_re_does_not_match(text):
	assert not GREETING_RE.match(text)


# [확인 20] route_question 1순위: state['error']가 있으면 인사말이어도 'error'
def test_route_question_error_first(fake_llm):
	assert route_question({"question": "안녕하세요", "error": "질문이 너무 깁니다"}) == "error"
	assert fake_llm.calls == []


# [확인 21] route_question 2순위: 인사·감사·잡담이면 'chat'
#           3단계 extract_keywords 전에 갈라지므로 search 모듈을 import하지도 않고, LLM도 부르지 않는다
@pytest.mark.parametrize("question", ["감사합니다!", "ㅋㅋ", "음...", "너는 누구야?", "이 서비스 기능", "오늘 날씨 어때?", "심심해", "잘 지내?"])
def test_route_question_chat_before_keywords(fake_llm, question):
	assert route_question({"question": question}) == "chat"
	assert "hri.retriever.search" not in sys.modules
	assert fake_llm.calls == []


# [확인 22] route_question 3순위: extract_keywords() 결과가 있으면 'rewrite' (LLM 호출 없음)
@pytest.mark.parametrize("question", ["2026년 GDP 성장률은?", "그거 전망은?", "안녕하세요, 금리 전망 알려주세요"])
def test_route_question_keywords_is_rewrite(fake_llm, question):
	assert route_question({"question": question}) == "rewrite"
	assert fake_llm.calls == []


# [확인 23] route_question 4순위: 잡담 정규식에도 안 맞고 키워드도 없으면('ㅇㅇ') LLM(temperature=0, RouteDecision)이 고른 값을 따른다
@pytest.mark.parametrize("llm_route", ["chat", "rewrite"])
def test_route_question_ambiguous_uses_llm(fake_llm, llm_route):
	fake_llm.route = llm_route
	assert route_question({"question": "ㅇㅇ"}) == llm_route
	assert fake_llm.calls == [{"temperature": 0}]
	assert fake_llm.schemas == [RouteDecision]
	assert fake_llm.inputs[0] == [
		{"role": "system", "content": ROUTE_SYSTEM_PROMPT},
		{"role": "user", "content": "ㅇㅇ"},
	]


# [확인 24] route_after_grade: has_context(docs)가 True면 'generate', 비었거나 없으면 'no_answer'
@pytest.mark.parametrize(("state", "expected"), [
	({"docs": ["근거 청크"]}, "generate"),
	({"docs": []}, "no_answer"),
	({}, "no_answer"),
])
def test_route_after_grade(state, expected):
	assert route_after_grade(state) == expected


# [확인 25] 그래프 실행: 인사·잡담은 LLM 답과 상관없이 chat → save_log, 애매한 말은 LLM이 고른 갈래로 간다
@pytest.mark.parametrize(("question", "llm_route", "expected"), [
	("안녕하세요", "rewrite", ["validate", "chat", "save_log"]),
	("오늘 날씨 어때?", "rewrite", ["validate", "chat", "save_log"]),
	("ㅇㅇ", "chat", ["validate", "chat", "save_log"]),
	("ㅇㅇ", "rewrite", ["validate", "rewrite", "retrieve", "grade", "no_answer", "save_log"]),
])
def test_qa_graph_chat_and_llm_routes(fake_llm, question, llm_route, expected):
	fake_llm.route = llm_route
	nodes = run_nodes(registry.get_qa_graph(), qa_input(question=question), {"configurable": {"thread_id": "s1"}})
	assert nodes == expected


# [확인 26] get_checkpointer(): InMemorySaver 하나만 만든다
def test_checkpointer_is_single_in_memory_saver():
	assert isinstance(get_checkpointer(), InMemorySaver)
	assert get_checkpointer() is get_checkpointer()


# [확인 27] thread_id(=session_id)별로 state가 따로 저장된다
def test_checkpointer_keeps_state_per_thread():
	graph = registry.get_qa_graph()
	a, b = {"configurable": {"thread_id": "session-a"}}, {"configurable": {"thread_id": "session-b"}}
	graph.invoke(qa_input(question="금리 전망은?", persona="입문자"), a)
	graph.invoke(qa_input(question="물가 전망은?", persona="전문가"), b)
	assert graph.get_state(a).values["persona"] == "입문자"
	assert graph.get_state(b).values["persona"] == "전문가"


# [확인 28] messages 외 필드는 이전 턴 값이 남는다 → ask_stream이 매 턴 초기화해야 하는 이유
#           1턴에서 docs가 있었으면, 2턴에 docs를 안 넣으면 남은 docs 때문에 generate로 가고 / docs=[]로 넣으면 no_answer로 간다
def test_previous_turn_fields_remain_unless_reset():
	graph = registry.get_qa_graph()
	config = {"configurable": {"thread_id": "session-c"}}
	graph.invoke(qa_input(question="금리 전망은?", docs=["1턴 근거"]), config)

	not_reset = run_nodes(graph, {"question": "물가 전망은?"}, config)
	assert "generate" in not_reset

	reset = run_nodes(graph, {"question": "물가 전망은?", "docs": []}, config)
	assert "no_answer" in reset and "generate" not in reset


# ─────────────────────────────────────────────────────────────
# 6. [3.8.5] 질의응답 답변 노드 · 기록 노드
# ─────────────────────────────────────────────────────────────

class FakeUsageHandler:
	"""ask_stream()이 config['configurable']['usage']로 넘기는 UsageMetadataCallbackHandler 흉내 (usage_metadata만 사용)."""

	def __init__(self, usage_metadata):
		self.usage_metadata = usage_metadata


# [확인 34] generate_node: get_context_docs → format_docs → build_messages(질문, [자료], 페르소나, 이전 대화) → LLM
#           {'answer', 'has_source': True, 'route': 'rag', 'context_docs', 'messages': [이번 질문, 이번 답변]}
def test_generate_node(fake_llm, qa_deps):
	history = [HumanMessage("지난 질문"), AIMessage("지난 답변")]
	state = qa_input(question="금리 전망은?", persona="입문자", docs=["청크A", "청크B"], messages=history)

	result = generate_node(state)

	assert qa_deps.context_calls == [["청크A", "청크B"]]                       # get_context_docs(state['docs'])
	call = qa_deps.build_calls[0]
	assert call["question"] == "금리 전망은?" and call["persona"] == "입문자"
	assert call["context"].startswith("[출처: 2026년 경제전망 p.1-2 / 1. 개요]")  # 진짜 format_docs 결과
	assert call["history"] == history                                         # 이번 질문은 아직 없는 이전 대화
	assert fake_llm.calls == [{}]                                             # get_chat_model() 기본값
	assert result["answer"] == "LLM 답변" and result["has_source"] is True and result["route"] == "rag"
	assert [d.text for d in result["context_docs"]] == ["근거 0", "근거 1"]
	assert [(type(m), m.content) for m in result["messages"]] == [(HumanMessage, "금리 전망은?"), (AIMessage, "LLM 답변")]


# [확인 35] no_answer·error_reply·chat 노드: 경로별 답변·route·has_source (no_answer·error_reply는 LLM 호출 없음)
def test_reply_nodes(fake_llm):
	no_answer = no_answer_node(qa_input(question="환율은?"))
	assert (no_answer["answer"], no_answer["route"], no_answer["has_source"]) == (NO_ANSWER_TEXT, "no_answer", False)
	assert NO_ANSWER_TEXT == "자료에 없습니다"

	error = error_reply_node(qa_input(question="", error="질문을 입력해 주세요."))
	assert (error["answer"], error["route"], error["has_source"]) == ("질문을 입력해 주세요.", "error", False)
	assert fake_llm.calls == [] and fake_llm.chat_inputs == []

	chat = chat_node(qa_input(question="안녕하세요"))
	assert (chat["answer"], chat["route"], chat["has_source"]) == (fake_llm.chat_answer, "chat", False)
	assert fake_llm.chat_inputs == ["안녕하세요"]
	for result in (no_answer, error, chat):
		assert [type(m) for m in result["messages"]] == [HumanMessage, AIMessage]


# [확인 36] save_log_node: 응답 시간·토큰 합계·원화 비용(cost_korean_won(usage, get_usd_krw()))으로 QALog를 저장하고,
#           모델마다 LlmUsage(flow='qa', ref_id=log_id)를 저장한 뒤 {'log_id'}를 돌려준다
def test_save_log_node_with_usage(fake_llm, qa_deps):
	usage = {
		"gpt-5-mini-2025-08-07": {"input_tokens": 1200, "output_tokens": 300, "total_tokens": 1500},
		"gpt-4o-mini": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
	}
	state = qa_input(answer="LLM 답변", route="rag", has_source=True, started_at=datetime.now() - timedelta(seconds=2))
	config = {"configurable": {"thread_id": "session-9", "usage": FakeUsageHandler(usage)}}

	result = save_log_node(state, config)

	log = qa_deps.logs[0]
	assert isinstance(log, QALog)
	assert (log.user_id, log.session_id, log.question, log.answer, log.persona) == ("tester", "session-9", "금리 전망은?", "LLM 답변", "실무자")
	assert (log.route, log.has_source, log.error) == ("rag", True, None)
	assert 2000 <= log.response_ms < 5000
	assert log.tokens == 1620 and log.cost_krw == 12.5
	assert fake_llm.cost_calls == [(usage, 1400.0)] and len(fake_llm.usd_calls) == 1   # 환율은 get_usd_krw()만 사용
	assert result == {"log_id": 101}
	assert [(u.flow, u.user_id, u.ref_id, u.model, u.input_tokens, u.output_tokens) for u in qa_deps.usages] == [
		("qa", "tester", 101, "gpt-5-mini-2025-08-07", 1200, 300),
		("qa", "tester", 101, "gpt-4o-mini", 100, 20),
	]
	assert all(isinstance(u, LlmUsage) for u in qa_deps.usages)


# [확인 37] save_log_node: LLM을 쓰지 않은 질문(입력 오류)은 환율을 조회하지 않고 0원·0토큰, 사용량 저장 없음,
#           qa_logs.error에 안내 문구가 기록된다
def test_save_log_node_error_without_usage(fake_llm, qa_deps):
	state = qa_input(question="", error="질문을 입력해 주세요.", answer="질문을 입력해 주세요.", route="error", has_source=False)
	save_log_node(state, {"configurable": {"thread_id": "s1", "usage": FakeUsageHandler({})}})

	log = qa_deps.logs[0]
	assert (log.route, log.error, log.tokens, log.cost_krw) == ("error", "질문을 입력해 주세요.", 0, 0.0)
	assert fake_llm.usd_calls == [] and fake_llm.cost_calls == [] and qa_deps.usages == []


# [확인 38] 그래프 전체: 근거 있음 → generate 답변이 state에 남고, save_log가 한 번 실행되어 log_id가 생긴다
#           같은 thread로 2턴 질문하면 messages가 4개(질문·답변 2쌍) 쌓이고, 2턴 generate는 1턴 대화를 이전 대화로 받는다
def test_qa_graph_generate_and_log(fake_llm, qa_deps):
	graph = registry.get_qa_graph()
	config = {"configurable": {"thread_id": "session-r", "usage": FakeUsageHandler({})}}

	first = graph.invoke(qa_input(question="금리 전망은?", docs=["청크A"]), config)
	assert (first["answer"], first["route"], first["has_source"], first["log_id"]) == ("LLM 답변", "rag", True, 101)

	second = graph.invoke(qa_input(question="물가 전망은?", docs=["청크B"]), config)
	assert len(second["messages"]) == 4
	assert [m.content for m in qa_deps.build_calls[1]["history"]] == ["금리 전망은?", "LLM 답변"]
	assert [log.route for log in qa_deps.logs] == ["rag", "rag"]


# [확인 39] 그래프 전체: 경로별로 save_log에 넘어가는 route·답변 (근거 없음·입력 오류·잡담)
@pytest.mark.parametrize(("inputs", "route", "answer"), [
	(qa_input(question="환율은?", docs=[]), "no_answer", NO_ANSWER_TEXT),
	(qa_input(question="", error="질문을 입력해 주세요."), "error", "질문을 입력해 주세요."),
	(qa_input(question="안녕하세요"), "chat", "안녕하세요! 무엇을 도와드릴까요?"),
], ids=["근거_없음", "입력_오류", "잡담"])
def test_qa_graph_routes_logged(fake_llm, qa_deps, inputs, route, answer):
	result = registry.get_qa_graph().invoke(inputs, {"configurable": {"thread_id": f"s-{route}"}})
	assert (result["route"], result["answer"]) == (route, answer)
	assert [(log.route, log.answer) for log in qa_deps.logs] == [(route, answer)]
