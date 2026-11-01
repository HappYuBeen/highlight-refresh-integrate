"""table_to_markdown()·split_big_table()·summarize_tables()와 insert_tables()(report_dao)가 명세대로 동작하는지 확인하는 테스트
(네트워크·DB·.env 없이 실행).

표 rows는 pdfplumber extract_tables()가 HRI 보고서에서 돌려주는 모양을 줄여서 흉내 낸다
(병합된 칸·빈 칸은 None, 칸 안 줄바꿈은 '\n').
"""

import logging
import sys
import threading
import time
import types

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

import hri.loader.table_loader as table_loader
from hri.dao.report_dao import insert_tables
from hri.domain.schemas import Chunk, Table
from hri.graph.ingest_graph import extract_tables_node, summarize_tables_node
from hri.loader.table_loader import split_big_table, summarize_tables, table_to_markdown


# 머리글 1행 + 본문 body_rows행 표. 본문 i행은 ['2000+i', 'i.0']
def make_rows(body_rows):
	return [["연도", "물가상승률"]] + [[str(2000 + i), f"{i}.0"] for i in range(body_rows)]


# 마크다운 표 한 줄의 칸 수 ('\|'는 글자라서 세지 않음)
def count_cells(line):
	return line.replace("\\|", "").count("|") - 1


# ---------------------------------------------------------------------
# table_to_markdown()
# ---------------------------------------------------------------------
# [확인 1] 머리글 → 구분선(|---|) → 본문 순서
def test_table_to_markdown_order():
	rows = [["구 분", "2025", "2026(E)"], ["경제성장률 (%)", "1.1", "3.5"], ["민간소비 (%)", "1.5", "2.4"]]
	assert table_to_markdown(rows) == (
		"| 구 분 | 2025 | 2026(E) |\n"
		"|---|---|---|\n"
		"| 경제성장률 (%) | 1.1 | 3.5 |\n"
		"| 민간소비 (%) | 1.5 | 2.4 |"
	)


# [확인 2] None(병합된 칸·빈 칸)은 빈칸, 칸 수는 그대로
def test_table_to_markdown_none_is_blank():
	rows = [["구 분", None, "2025"], [None, "민간소비 (%)", "1.5"]]
	lines = table_to_markdown(rows).split("\n")
	assert lines[0] == "| 구 분 |  | 2025 |"
	assert lines[2] == "|  | 민간소비 (%) | 1.5 |"
	assert "None" not in "\n".join(lines)


# [확인 3] 칸 안의 줄바꿈·연속 공백은 공백 1칸으로 (표 한 행이 마크다운 한 줄로 유지됨)
def test_table_to_markdown_newline_in_cell():
	rows = [["구분", "내용"], ["1. 포퓰리즘(populism)\n의 시대", "경제\n및  사회 환경의 급변 "]]
	lines = table_to_markdown(rows).split("\n")
	assert len(lines) == 3
	assert lines[2] == "| 1. 포퓰리즘(populism) 의 시대 | 경제 및 사회 환경의 급변 |"


# [확인 4] 칸 안의 '|'는 '\|'로 바꿔 칸이 늘어나지 않게
def test_table_to_markdown_pipe_in_cell():
	rows = [["구분", "값"], ["A|B", "1"]]
	lines = table_to_markdown(rows).split("\n")
	assert lines[2] == "| A\\|B | 1 |"
	assert {count_cells(line) for line in (lines[0], lines[2])} == {2}


# [확인 5] 숫자를 넘겨도 글자로 바꿔 표를 만든다 (비교표·대시보드에서 쓸 때)
def test_table_to_markdown_number_cells():
	assert table_to_markdown([["지표", "값"], ["성장률", 2.4], ["건수", 7]]).split("\n")[2:] == ["| 성장률 | 2.4 |", "| 건수 | 7 |"]


# [확인 6] 빈 rows → "", 머리글만 있으면 머리글 + 구분선
def test_table_to_markdown_empty_and_header_only():
	assert table_to_markdown([]) == ""
	assert table_to_markdown([["연도", "물가상승률"]]) == "| 연도 | 물가상승률 |\n|---|---|"


# ---------------------------------------------------------------------
# split_big_table()
# ---------------------------------------------------------------------
# [확인 7] 본문 23행 → 10·10·3행 3조각, 모든 조각 첫 줄이 머리글, 본문 순서·내용은 그대로
def test_split_big_table_pieces():
	rows = make_rows(23)
	pieces = split_big_table(rows)
	assert [len(p) for p in pieces] == [11, 11, 4]
	assert all(p[0] == ["연도", "물가상승률"] for p in pieces)
	assert [row for p in pieces for row in p[1:]] == rows[1:]


# [확인 8] 경계: 본문 10행은 나누지 않고, 11행부터 나눈다 (기준은 머리글을 뺀 본문 행 수)
@pytest.mark.parametrize("body_rows, sizes", [(0, [1]), (3, [4]), (10, [11]), (11, [11, 2])])
def test_split_big_table_boundary(body_rows, sizes):
	rows = make_rows(body_rows)
	pieces = split_big_table(rows)
	assert [len(p) for p in pieces] == sizes
	if len(pieces) == 1:
		assert pieces == [rows]


# [확인 9] max_rows를 바꾸면 그 행 수로 나눈다
def test_split_big_table_max_rows():
	pieces = split_big_table(make_rows(5), max_rows=2)
	assert [[row[0] for row in p] for p in pieces] == [["연도", "2000", "2001"], ["연도", "2002", "2003"], ["연도", "2004"]]


# [확인 10] 빈 rows → [], max_rows가 1보다 작으면 ValueError (표가 사라지지 않게)
def test_split_big_table_empty_and_bad_max_rows():
	assert split_big_table([]) == []
	for bad in (0, -1):
		with pytest.raises(ValueError):
			split_big_table(make_rows(3), max_rows=bad)


# ---------------------------------------------------------------------
# 두 함수를 이어서: 잘린 조각에도 머리글이 붙어 나오는지
# ---------------------------------------------------------------------
# HRI 보고서 '세계 경제 전망' 표 모양 (머리글 1행 + 본문 14행, 병합 칸은 None)
WORLD_TABLE = [["구 분", None, "2024", "2025", "2026(E)", "2027(E)"]] + [
	["세계 경제성장률" if i == 0 else None, f"국가{i}", "3.5", "3.5", "3.0", "3.4"] for i in range(14)
]


# [확인 11] 조각마다 table_to_markdown() → 모든 조각이 '머리글 줄 + 구분선'으로 시작하고, 줄마다 칸 수가 같다
def test_split_then_markdown_keeps_header():
	header_line = "| 구 분 |  | 2024 | 2025 | 2026(E) | 2027(E) |"
	markdowns = [table_to_markdown(p) for p in split_big_table(WORLD_TABLE)]
	assert len(markdowns) == 2
	for md in markdowns:
		lines = md.split("\n")
		assert lines[0] == header_line
		assert lines[1] == "|---|---|---|---|---|---|"
		assert {count_cells(line) for line in lines if line != lines[1]} == {6}
	assert "| 세계 경제성장률 | 국가0 |" in markdowns[0]
	assert "| 세계 경제성장률" not in markdowns[1]


# [확인 12] 표 노드(extract_tables_node)에 진짜 두 함수를 연결: 큰 표는 '(1/2)'·'(2/2)' 조각마다 머리글이 있는 마크다운
#           extract_tables·validate_table은 아직 레포에 없어서(3.2.10) 이 테스트에서만 가짜로 넣는다
def test_extract_tables_node_with_real_functions(monkeypatch):
	big = Table(page=12, caption="< 세계 경제 전망 >", rows=[["" if c is None else c for c in r] for r in WORLD_TABLE])
	small = Table(page=3, caption="[표 1]", rows=make_rows(2))
	monkeypatch.setattr(table_loader, "extract_tables", lambda file_path: [small, big], raising=False)
	monkeypatch.setattr(table_loader, "validate_table", lambda rows: "✅ 이상 없음", raising=False)

	tables = extract_tables_node({"file_path": "report.pdf"})["tables"]

	assert [t.caption for t in tables] == ["[표 1]", "< 세계 경제 전망 > (1/2)", "< 세계 경제 전망 > (2/2)"]
	assert [len(t.rows) for t in tables] == [3, 11, 5]
	assert tables[0].markdown == table_to_markdown(make_rows(2))
	assert all(t.markdown.startswith("| 구 분 |  | 2024 | 2025 | 2026(E) | 2027(E) |\n|---|") for t in tables[1:])


# ---------------------------------------------------------------------
# [추가 기능 · 명세서 밖] split_big_table(header_rows=...): 머리글이 2줄 이상인 표
# ---------------------------------------------------------------------
# HRI '경제 전망' 표 모양 (머리글 2줄 + 본문 12행). 1줄은 연도, 2줄은 상반·하반·연간 (병합 칸은 빈칸)
OUTLOOK_HEADER = [
	["구 분", "", "2025", "2026", "", "", "2027(E)", "", ""],
	["", "", "", "상반", "하반(E)", "연간(E)", "상반", "하반", "연간"],
]
OUTLOOK_TABLE = OUTLOOK_HEADER + [[f"지표{i}", "", "1.1", "3.8", "3.2", "3.5", "2.2", "2.5", "2.4"] for i in range(12)]


# [확인 13] header_rows=2: 모든 조각 앞에 머리글 2줄이 붙고, 본문은 머리글 2줄을 뺀 12행을 10·2행으로 나눈다
def test_split_big_table_two_header_rows():
	pieces = split_big_table(OUTLOOK_TABLE, header_rows=2)
	assert [len(p) for p in pieces] == [12, 4]
	assert all(p[:2] == OUTLOOK_HEADER for p in pieces)
	assert [row for p in pieces for row in p[2:]] == OUTLOOK_TABLE[2:]
	# 마크다운으로 바꾸면 둘째 조각에도 '상반 | 하반(E) | 연간(E)' 줄이 남는다 (마크다운 머리글은 1줄이라 2줄째는 구분선 아래 첫 줄)
	lines = table_to_markdown(pieces[1]).split("\n")
	assert lines[2] == "|  |  |  | 상반 | 하반(E) | 연간(E) | 상반 | 하반 | 연간 |"


# [확인 14] 기본값(header_rows=1)은 명세서대로 첫 줄만 붙인다 → 같은 표의 둘째 조각에는 '상반·하반' 줄이 없다 (추가 기능을 둔 이유)
def test_split_big_table_default_is_one_header_row():
	pieces = split_big_table(OUTLOOK_TABLE)
	assert split_big_table(OUTLOOK_TABLE, header_rows=1) == pieces
	assert [len(p) for p in pieces] == [11, 4]
	assert pieces[1][0] == OUTLOOK_HEADER[0] and OUTLOOK_HEADER[1] not in pieces[1]


# [확인 15] header_rows도 본문 행 수 경계에 들어간다 (머리글 2줄 + 본문 10행은 나누지 않음), 1보다 작으면 ValueError
def test_split_big_table_header_rows_boundary_and_bad_value():
	table = OUTLOOK_TABLE[:12]
	assert split_big_table(table, header_rows=2) == [table]
	for bad in (0, -1):
		with pytest.raises(ValueError):
			split_big_table(OUTLOOK_TABLE, header_rows=bad)


# =====================================================================
# WBS 3.2.11 summarize_tables() · insert_tables()
# =====================================================================
# summarize_tables()는 함수 안에서 hri.llm.llm_client를 import한다.
# 진짜 llm_client는 import할 때 .env가 필요하고 OpenAI를 부르므로, test_graphs와 같이 가짜 모듈을 sys.modules에 끼운다.
# 가짜 모델은 받은 메시지 글로 응답을 정한다 (batch가 여러 스레드로 돌아 순서가 섞여도 표마다 같은 답이 나오게).
#   - 글에 'FAIL'이 있으면 예외, 'EMPTY'가 있으면 빈 응답, 그 밖에는 '요약: <표 제목 줄>'
class _SummaryChatModel(BaseChatModel):
	"""요약 테스트용 가짜 채팅 모델. 받은 메시지·동시 실행 개수(peak)를 기록한다."""

	delay: float = 0.0
	calls: list = Field(default_factory=list)
	running: int = 0
	peak: int = 0

	@property
	def _llm_type(self) -> str:
		return "fake-summary-chat"

	def _generate(self, messages, stop=None, run_manager=None, **kwargs):
		with _LOCK:
			self.calls.append(messages)
			self.running += 1
			self.peak = max(self.peak, self.running)
		try:
			time.sleep(self.delay)
			human = messages[-1].content
			if "FAIL" in human:
				raise RuntimeError("API 오류")
			text = "" if "EMPTY" in human else "요약: " + human.split("\n")[0]
			return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])
		finally:
			with _LOCK:
				self.running -= 1


_LOCK = threading.Lock()


@pytest.fixture
def fake_llm(monkeypatch):
	"""가짜 llm_client 모듈을 끼우고, get_chat_model에 넘긴 인자(kwargs)와 모델을 돌려준다."""
	model = _SummaryChatModel()
	used = {"kwargs": []}

	def get_chat_model(**kwargs):
		used["kwargs"].append(kwargs)
		return model

	module = types.ModuleType("hri.llm.llm_client")
	module.get_chat_model = get_chat_model
	monkeypatch.setitem(sys.modules, "hri.llm.llm_client", module)
	used["model"] = model
	return used


def make_table(page, caption, markdown="| 연도 | 물가상승률 |\n|---|---|\n| 2025 | 2.1 |", **kwargs):
	return Table(page=page, caption=caption, rows=[["연도", "물가상승률"]], markdown=markdown, **kwargs)


# ---------------------------------------------------------------------
# summarize_tables()
# ---------------------------------------------------------------------
# [확인 16] 표마다 summary(검색용)를 채운다. 순서·doc_id·markdown(답변용 원본)·rows는 그대로, get_chat_model(temperature=0)
def test_summarize_tables_fills_summary_only(fake_llm):
	tables = [make_table(3, "[표 1] 물가"), make_table(5, "[표 2] 성장률"), make_table(7, "< 고용 >")]
	result = summarize_tables(tables)

	assert [t.summary for t in result] == ["요약: 표 제목: [표 1] 물가", "요약: 표 제목: [표 2] 성장률", "요약: 표 제목: < 고용 >"]
	for before, after in zip(tables, result):
		assert after.model_dump(exclude={"summary"}) == before.model_dump(exclude={"summary"})
	assert all(t.summary is None for t in tables)          # 입력 목록은 바꾸지 않는다
	assert fake_llm["kwargs"] == [{"temperature": 0}]      # 모델은 한 번만 만든다


# [확인 17] LLM에 보내는 글: 표 제목(캡션) + 마크다운 원본. 차트에만 '제목·단위·추세·최댓값/최솟값' 규칙이 붙는다
def test_summarize_tables_prompt_table_and_chart(fake_llm):
	chart_md = "| 연도 | 성장률 |\n|---|---|\n| 2024 | 2.0 |\n| 2025 | 1.0 |"
	table, chart = make_table(3, "[표 1] 물가"), make_table(9, "[그림 1] 성장률 (%)", markdown=chart_md, source_type="chart")
	summarize_tables([table, chart])

	sent = {msgs[-1].content.split("\n")[0]: msgs for msgs in fake_llm["model"].calls}
	table_msgs, chart_msgs = sent["표 제목: [표 1] 물가"], sent["표 제목: [그림 1] 성장률 (%)"]
	assert table.markdown in table_msgs[-1].content and chart_md in chart_msgs[-1].content
	assert table_loader.CHART_SUMMARY_RULE not in table_msgs[0].content
	assert table_loader.CHART_SUMMARY_RULE in chart_msgs[0].content
	for word in ("제목", "단위", "추세", "최댓값", "최솟값"):
		assert word in table_loader.CHART_SUMMARY_RULE


# [확인 18] 캡션이 없는 표는 '(제목 없음)'으로 보낸다 (None 글자가 들어가지 않게)
def test_summarize_tables_without_caption(fake_llm):
	summarize_tables([make_table(3, None)])
	human = fake_llm["model"].calls[0][-1].content
	assert human.startswith("표 제목: (제목 없음)") and "None" not in human


# [확인 19] 표가 0개면 LLM을 만들지도 부르지도 않고 []
def test_summarize_tables_empty(fake_llm):
	assert summarize_tables([]) == []
	assert fake_llm["kwargs"] == [] and fake_llm["model"].calls == []


# [확인 20] 실패(API 오류·빈 응답)한 표만 summary=None, 나머지는 계속 요약하고 로그에 '요약에 실패한 표입니다'
def test_summarize_tables_failure_is_none(fake_llm, caplog):
	tables = [make_table(3, "[표 1] 물가"), make_table(5, "[표 2] FAIL"), make_table(7, "[표 3] EMPTY"), make_table(8, "[표 4] 고용")]
	with caplog.at_level(logging.ERROR, logger="hri.loader.table_loader"):
		result = summarize_tables(tables)

	assert [t.summary is None for t in result] == [False, True, True, False]
	assert [t.doc_id for t in result] == [t.doc_id for t in tables]
	failed = [r.getMessage() for r in caplog.records]
	assert failed == ["5쪽 표 [표 2] FAIL: 요약에 실패한 표입니다", "7쪽 표 [표 3] EMPTY: 요약에 실패한 표입니다 (빈 응답)"]


# [확인 21] batch로 동시에 최대 4개씩 보낸다 (표 10개, 1개에 0.05초)
def test_summarize_tables_max_concurrency(fake_llm):
	fake_llm["model"].delay = 0.05
	result = summarize_tables([make_table(i, f"[표 {i}]") for i in range(1, 11)])
	assert len(fake_llm["model"].calls) == 10 and all(t.summary for t in result)
	assert fake_llm["model"].peak == table_loader.SUMMARY_MAX_CONCURRENCY == 4


# [확인 22] 요약 노드(summarize_tables_node)에 진짜 summarize_tables를 연결:
#           성공한 표 청크는 '캡션\n요약', 실패한 표 청크는 '캡션\n원본 마크다운'이고 warnings로 화면에 알린다
def test_summarize_tables_node_with_real_function(fake_llm):
	ok, bad = make_table(3, "[표 1] 물가"), make_table(5, "[표 2] FAIL")
	chunks = [
		Chunk(chunk_no=0, section_no=0, page=1, text="본문"),
		Chunk(chunk_no=1, section_no=0, page=3, chunk_type="table", text="(요약 전)", doc_id=ok.doc_id),
		Chunk(chunk_no=2, section_no=0, page=5, chunk_type="table", text="(요약 전)", doc_id=bad.doc_id),
	]
	result = summarize_tables_node({"tables": [ok, bad], "chunks": chunks})

	assert [c.text for c in result["chunks"]] == ["본문", "[표 1] 물가\n요약: 표 제목: [표 1] 물가", "[표 2] FAIL\n" + bad.markdown]
	assert result["warnings"] == ["5쪽 표 [표 2] FAIL: 요약에 실패한 표입니다 (원본 표로 검색합니다)"]
	assert [t.summary is None for t in result["summarized_tables"]] == [False, True]


# [확인 23] 모두 성공하면 warnings 키를 돌려주지 않는다 (기존 [확인 5] 반환 키와 같게)
def test_summarize_tables_node_no_warning_when_ok(fake_llm):
	table = make_table(3, "[표 1] 물가")
	chunk = Chunk(chunk_no=0, section_no=0, page=3, chunk_type="table", text="(요약 전)", doc_id=table.doc_id)
	assert set(summarize_tables_node({"tables": [table], "chunks": [chunk]})) == {"summarized_tables", "chunks"}


# ---------------------------------------------------------------------
# insert_tables() (hri/dao/report_dao.py)
# ---------------------------------------------------------------------
class _RecordingConn:
	"""persist_node가 넘기는 pymysql 연결 흉내. executemany에 받은 SQL·값과 commit·rollback 호출을 기록한다."""

	def __init__(self):
		self.executed = []
		self.events = []

	def cursor(self):
		return self

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return False

	def executemany(self, sql, rows):
		self.executed.append((sql, rows))

	def commit(self):
		self.events.append("commit")

	def rollback(self):
		self.events.append("rollback")


# [확인 24] report_tables에 10칸을 넣는다: report_id는 인자 값, confidence → ocr_confidence, 원본 markdown·요약 summary 모두
def test_insert_tables_rows(monkeypatch):
	table = make_table(3, "[표 1] 물가", summary="물가 요약")
	chart = make_table(9, "[그림 1] 성장률", source_type="chart", image_path="data/charts/raw/a.png", confidence=0.8, needs_review=True)
	conn = _RecordingConn()

	assert insert_tables(conn, 7, [table, chart]) == 2

	(sql, rows), = conn.executed
	sql = " ".join(sql.split())
	assert sql.startswith("INSERT INTO report_tables (doc_id, report_id, page, caption, markdown, summary, source_type, image_path, ocr_confidence, needs_review) VALUES (")
	assert sql.count("%s") == 10
	assert rows == [
		(table.doc_id, 7, 3, "[표 1] 물가", table.markdown, "물가 요약", "table", None, None, False),
		(chart.doc_id, 7, 9, "[그림 1] 성장률", chart.markdown, None, "chart", "data/charts/raw/a.png", 0.8, True),
	]


# [확인 25] commit·rollback을 하지 않는다 (persist_node가 한 트랜잭션으로 처리), 빈 목록이면 0이고 DB를 부르지 않는다
def test_insert_tables_no_commit_and_empty():
	conn = _RecordingConn()
	insert_tables(conn, 7, [make_table(3, "[표 1]")])
	assert conn.events == []
	empty = _RecordingConn()
	assert insert_tables(empty, 7, []) == 0 and empty.executed == []
