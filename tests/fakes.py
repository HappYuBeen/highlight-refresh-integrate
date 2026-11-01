"""테스트용 가짜 모델 · FakeToolChatModel: bind_tools·with_structured_output 구현 (엄정현)

langchain의 GenericFakeChatModel은 bind_tools를 구현하지 않고, with_structured_output도 bind_tools를 쓰므로
도구를 붙이는 그래프(agent_graph)·구조화 출력을 쓰는 함수(route_question 등)를 테스트할 수 없다.
FakeToolChatModel은 미리 넣어 둔 응답을 차례로 돌려주고, 받은 메시지·붙인 도구·요청한 양식을 기록한다 (네트워크·API 키 없이 실행).

사용
	fake = FakeToolChatModel(responses=[AIMessage(content="", tool_calls=[...]), AIMessage(content="완료")])
	get_chat_model을 lambda **kw: fake 로 바꿔 끼운 뒤 그래프를 실행한다.
"""

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import Field


class FakeToolChatModel(BaseChatModel):
	"""미리 넣은 응답(AIMessage 또는 Pydantic 객체)을 순서대로 돌려주는 가짜 채팅 모델.

	- invoke()      : responses에서 AIMessage를 하나 꺼내 돌려준다 (tool_calls가 있으면 도구 호출 응답)
	- bind_tools()  : 붙인 도구를 bound_tools에 기록하고 자기 자신을 돌려준다
	- with_structured_output(schema): responses에서 Pydantic 객체를 하나 꺼내 돌려주는 Runnable을 돌려준다
	- calls         : 모델이 받은 메시지 목록을 호출마다 기록한다
	응답이 떨어지면 AssertionError로 알려 준다 (예상보다 LLM을 더 부른 것).
	"""

	responses: list[Any] = Field(default_factory=list)
	calls: list[list[BaseMessage]] = Field(default_factory=list)
	bound_tools: list[Any] = Field(default_factory=list)
	structured_schemas: list[Any] = Field(default_factory=list)

	@property
	def _llm_type(self) -> str:
		return "fake-tool-chat"

	def _next(self) -> Any:
		assert self.responses, "FakeToolChatModel: 미리 넣은 응답을 다 썼습니다 (예상보다 LLM을 더 불렀습니다)"
		return self.responses.pop(0)

	def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
		self.calls.append(list(messages))
		response = self._next()
		assert isinstance(response, AIMessage), f"invoke 차례인데 AIMessage가 아닌 {type(response).__name__}가 들어 있습니다"
		# 같은 객체를 다시 쓰면 add_messages가 같은 id로 보고 덮어쓰므로 복사해서 돌려준다
		return ChatResult(generations=[ChatGeneration(message=response.model_copy())])

	def bind_tools(self, tools: list[Any], **kwargs: Any) -> "FakeToolChatModel":
		self.bound_tools = list(tools)
		return self

	def with_structured_output(self, schema: Any, **kwargs: Any) -> RunnableLambda:
		self.structured_schemas.append(schema)

		def _respond(messages: Any) -> Any:
			self.calls.append(list(messages) if isinstance(messages, list) else [messages])
			response = self._next()
			assert isinstance(response, schema), f"{schema.__name__} 차례인데 {type(response).__name__}가 들어 있습니다"
			return response

		return RunnableLambda(_respond)


# ─────────────────────────────────────────────────────────────
# 업로드 그래프 저장(persist) 테스트용 가짜 DB·벡터DB (WBS 3.8.3)
# ─────────────────────────────────────────────────────────────

class FakeConnection:
	"""get_connection()이 돌려주는 pymysql 연결 흉내. commit·rollback·close 호출을 events에 기록한다.

	with 문으로 쓰면 블록이 끝날 때 close()가 불린다 (진짜 pymysql 연결과 같음).
	"""

	def __init__(self, events: list):
		self.events = events

	def commit(self):
		self.events.append("commit")

	def rollback(self):
		self.events.append("rollback")

	def close(self):
		self.events.append("close")

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		self.close()
		return False


class FakeIngestStore:
	"""persist 노드가 부르는 DB·벡터DB 함수의 가짜. 호출 순서를 events에, 인자를 calls에 기록한다.

	- fail_at: 이 이름의 단계에서 RuntimeError를 낸다 (rollback 확인용).
	  'insert_report' · 'insert_sections' · 'insert_tables' · 'insert_chunks' · 'add_chunks' · 'commit' · 'delete_report_vectors'
	- report_id: insert_report가 돌려줄 번호
	"""

	def __init__(self, report_id: int = 7, fail_at: str | None = None):
		self.report_id = report_id
		self.fail_at = fail_at
		self.events: list = []
		self.calls: dict = {}
		self.connections: list = []

	def _step(self, name: str, *args):
		self.events.append(name)
		self.calls[name] = args
		if self.fail_at == name:
			raise RuntimeError(f"{name} 실패")

	def get_connection(self):
		self.events.append("get_connection")
		conn = FakeConnection(self.events)
		if self.fail_at == "commit":
			def failing_commit():
				self.events.append("commit")
				raise RuntimeError("commit 실패")
			conn.commit = failing_commit
		self.connections.append(conn)
		return conn

	def insert_report(self, conn, report):
		self._step("insert_report", conn, report)
		return self.report_id

	def insert_sections(self, conn, report_id, sections):
		self._step("insert_sections", conn, report_id, sections)
		return len(sections)

	def insert_tables(self, conn, report_id, tables):
		self._step("insert_tables", conn, report_id, tables)
		return len(tables)

	def insert_chunks(self, conn, report_id, chunks):
		self._step("insert_chunks", conn, report_id, chunks)
		return len(chunks)

	def add_chunks(self, chunks):
		self._step("add_chunks", chunks)
		return len(chunks)

	def delete_report_vectors(self, report_id):
		self._step("delete_report_vectors", report_id)


def install_ingest_store(monkeypatch, store: FakeIngestStore, chunk_size: int = 500, chunk_overlap: int = 50) -> FakeIngestStore:
	"""split·persist 노드가 부르는 진짜 모듈의 함수를 가짜로 바꿔 끼운다 (DB·Chroma·.env 없이 실행).

	- hri.config.settings.load_settings : CHUNK_SIZE·CHUNK_OVERLAP만 쓰는 가짜 Settings
	- hri.dao.db.get_connection · hri.dao.report_dao.insert_* · hri.vector_store.chroma_store.add_chunks·delete_report_vectors
	"""
	import hri.config.settings as settings_module
	import hri.dao.db as db
	import hri.dao.report_dao as report_dao
	import hri.vector_store.chroma_store as chroma_store
	from hri.domain.schemas import Settings

	settings = Settings(mysql_host="localhost", mysql_user="test", mysql_password="test", mysql_database="test",
		chunk_size=chunk_size, chunk_overlap=chunk_overlap)
	monkeypatch.setattr(settings_module, "load_settings", lambda: settings)
	monkeypatch.setattr(db, "get_connection", store.get_connection)
	for name in ("insert_report", "insert_sections", "insert_tables", "insert_chunks"):
		monkeypatch.setattr(report_dao, name, getattr(store, name), raising=False)
	for name in ("add_chunks", "delete_report_vectors"):
		monkeypatch.setattr(chroma_store, name, getattr(store, name))
	return store


# ─────────────────────────────────────────────────────────────
# 업로드 추출 노드(3.8.2)용 가짜: 아직 레포에 없는 표·차트·보고서 정보 함수
# ─────────────────────────────────────────────────────────────
class FakeExtractors:
	"""load_text·extract_tables·extract_charts·process_charts·summarize_tables 노드가 부르는 함수 중
	아직 레포에 없는 것(엄정현 3.2.7·3.2.10~3.2.15)의 가짜. 부른 순서·인자를 기록한다.

	- tables      : extract_tables()가 돌려줄 표 목록 (rows만 채운 Table)
	- charts      : extract_charts()가 돌려줄 ChartImage 목록
	- ocr_fail    : ocr_chart()가 ChartExtractError를 낼 차트의 image_path 집합
	- vision_fail : chart_to_table()이 ChartExtractError를 낼 차트의 image_path 집합
	- vision_crash: chart_to_table()이 예상 밖 오류(ValueError)를 낼 차트의 image_path 집합
	- vision_delay: chart_to_table() 1번에 걸리는 시간(초). 동시 실행 개수(peak)를 재려고 쓴다
	- meta        : extract_report_meta()가 돌려줄 dict

	가짜 규칙 (명세서 설명을 줄여 흉내 냄)
	- validate_table : 빈 칸("")이 있으면 '⚠️ 빈 칸 n개', 없으면 '✅ 이상 없음'
	- split_big_table: 머리글(첫 행)을 조각마다 붙이고 본문을 max_rows(기본 10)행씩 나눔
	- table_to_markdown: '| a | b |' 줄을 이어 붙임
	- chart_to_markdown: Table(source_type='chart', caption=차트 캡션)
	"""

	def __init__(self, tables=None, charts=None, ocr_fail=(), vision_fail=(), vision_crash=(), vision_delay=0.0, meta=None):
		import threading

		self.tables = list(tables or [])
		self.charts = list(charts or [])
		self.ocr_fail = set(ocr_fail)
		self.vision_fail = set(vision_fail)
		self.vision_crash = set(vision_crash)
		self.vision_delay = vision_delay
		self.meta = meta or {"title": "테스트 보고서", "publisher": "현대경제연구원", "published_date": None}
		self.calls: list = []          # (함수 이름, 주요 인자) 순서대로
		self.meta_pages: list = []     # extract_report_meta()가 받은 pages
		self.ocr_order: list = []      # ocr_chart를 부른 image_path 순서
		self.running = 0               # 지금 실행 중인 chart_to_table 개수
		self.peak = 0                  # 동시에 실행된 chart_to_table 최대 개수
		self._lock = threading.Lock()

	# pdf_loader
	def extract_report_meta(self, file_path, pages):
		self.calls.append(("extract_report_meta", file_path, [p.page for p in pages]))
		self.meta_pages = list(pages)  # 정제 전 pages인지 확인용 (test_load_text_node)
		return dict(self.meta)

	# table_loader
	def extract_tables(self, file_path):
		self.calls.append(("extract_tables", file_path))
		return list(self.tables)

	def validate_table(self, rows):
		blanks = sum(cell == "" for row in rows for cell in row)
		return f"⚠️ 빈 칸 {blanks}개" if blanks else "✅ 이상 없음"

	def split_big_table(self, rows, max_rows=10):
		header, body = rows[0], rows[1:]
		if len(body) <= max_rows:
			return [rows]
		return [[header] + body[i:i + max_rows] for i in range(0, len(body), max_rows)]

	def table_to_markdown(self, rows):
		return "\n".join("| " + " | ".join(row) + " |" for row in rows)

	def summarize_tables(self, tables):
		self.calls.append(("summarize_tables", len(tables)))
		return [t.model_copy(update={"summary": f"{t.caption} 요약"}) for t in tables]

	# chart_loader
	def extract_charts(self, file_path):
		self.calls.append(("extract_charts", file_path))
		return list(self.charts)

	def ocr_chart(self, image_path):
		from hri.domain.schemas import OcrToken
		from hri.exception.errors import ChartExtractError

		self.ocr_order.append(image_path)
		if image_path in self.ocr_fail:
			raise ChartExtractError(f"{image_path}: OCR 실패")
		return [OcrToken(text="2.0", bbox=(0, 0, 10, 10), conf=0.9, is_number=True)]

	def chart_to_table(self, chart, tokens):
		import time

		from hri.domain.schemas import ChartData, ChartSeries
		from hri.exception.errors import ChartExtractError

		with self._lock:
			self.running += 1
			self.peak = max(self.peak, self.running)
		try:
			time.sleep(self.vision_delay)
			if chart.image_path in self.vision_fail:
				raise ChartExtractError(f"{chart.page}쪽 차트: Vision 응답을 ChartData로 바꾸지 못함")
			if chart.image_path in self.vision_crash:
				raise ValueError(f"{chart.page}쪽 차트: 예상 밖 오류")
			return ChartData(chart_type="bar", x_labels=["2025"], series=[ChartSeries(name="성장률", values=[2.0])])
		finally:
			with self._lock:
				self.running -= 1

	def validate_chart(self, data, tokens):
		return data.model_copy(update={"confidence": 1.0})

	def chart_to_markdown(self, data, chart):
		from hri.domain.schemas import Table

		return Table(page=chart.page, caption=chart.caption, markdown="| x | 성장률 |\n|---|---|\n| 2025 | 2.0 |",
			source_type="chart", image_path=chart.image_path, confidence=data.confidence)


def install_extract_stubs(monkeypatch, extractors: FakeExtractors) -> FakeExtractors:
	"""아직 레포에 없는 추출 함수를 진짜 모듈 자리에 끼운다 (raising=False: 함수가 없어도 끼움).

	- hri.loader.pdf_loader.extract_report_meta (3.2.12)
	- hri.loader.table_loader: extract_tables·validate_table·split_big_table·table_to_markdown·summarize_tables (3.2.7·3.2.10·3.2.11)
	- hri.loader.chart_loader: extract_charts·ocr_chart·chart_to_table·validate_chart·chart_to_markdown (3.2.13~3.2.15)
	load_pdf·clean_text·find_body_start는 레포에 있으므로 진짜를 쓴다.
	함수가 레포에 들어오면 이 가짜 대신 진짜로 테스트하도록 바꾼다.
	"""
	import hri.loader.chart_loader as chart_loader
	import hri.loader.pdf_loader as pdf_loader
	import hri.loader.table_loader as table_loader

	monkeypatch.setattr(pdf_loader, "extract_report_meta", extractors.extract_report_meta, raising=False)
	for name in ("extract_tables", "validate_table", "split_big_table", "table_to_markdown", "summarize_tables"):
		monkeypatch.setattr(table_loader, name, getattr(extractors, name), raising=False)
	for name in ("extract_charts", "ocr_chart", "chart_to_table", "validate_chart", "chart_to_markdown"):
		monkeypatch.setattr(chart_loader, name, getattr(extractors, name), raising=False)
	return extractors
