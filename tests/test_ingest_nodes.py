"""업로드 그래프 노드 테스트 (엄정현)

[3.8.3] split_node, summarize_tables_node, persist_node
[3.8.2] load_text_node, extract_tables_node, process_charts_node + sample_report로 전체 업로드 1회 실행

네트워크·DB·Chroma·LLM·.env 없이 실행한다.
- load_pdf·clean_text·find_body_start(엄정현 pdf_loader), split_sections·split_text(김희진 text_splitter)는 진짜 함수를 쓴다
- 설정(CHUNK_SIZE·CHUNK_OVERLAP)·DB·Chroma는 tests/fakes.py의 FakeIngestStore로 바꿔 끼운다
- 아직 레포에 없는 함수(extract_report_meta, table_loader·chart_loader 함수들, summarize_tables)는
  tests/fakes.py의 FakeExtractors로 바꿔 끼운다

실행: poetry run python -m pytest tests/test_ingest_nodes.py -v
     (전체 업로드 1회 실행의 청크 수를 보려면 -s를 붙인다)
"""

import sys
import types
from datetime import date

import pymupdf
import pytest

import hri.loader.table_loader as table_loader
from hri.domain.schemas import ChartImage, Chunk, Page, Report, Section, Table, ValidationResult
from hri.graph import registry
from hri.graph.ingest_graph import (
	CHART_MAX_CONCURRENCY,
	NO_SECTION_WARNING,
	PENDING_TABLE_TEXT,
	SCANNED_PAGE_WARNING,
	TABLE_WARNING,
	extract_charts_node,
	extract_tables_node,
	load_text_node,
	persist_node,
	process_charts_node,
	split_node,
	summarize_tables_node,
)
from tests.fakes import FakeExtractors, FakeIngestStore, install_extract_stubs, install_ingest_store


# 목차 제목 3개(1·2·3)가 있는 5쪽짜리 본문. 섹션: 1쪽(1. 개요) · 2~3쪽(2. 경기 동향) · 5쪽(3. 전망)
PAGES = [
	Page(page=1, text="1. 개요\n올해 경제는 완만하게 회복하였다."),
	Page(page=2, text="2. 경기 동향\n소비가 늘어나고 있다.\n수출도 증가하였다."),
	Page(page=3, text="투자는 아직 부진하다."),
	Page(page=5, text="3. 전망\n내년 성장률은 2%대로 예상된다."),
]


def make_table(page, source_type="table", caption="< 주요 지표 >"):
	return Table(page=page, caption=caption, markdown="| 지표 | 값 |\n|---|---|\n| 성장률 | 2.0 |", source_type=source_type)


# ─────────────────────────────────────────────────────────────
# 1. split_node
# ─────────────────────────────────────────────────────────────

# [확인 1] 섹션 3개, 섹션별 텍스트 청크, 표·차트 청크(쪽이 속한 섹션 번호·doc_id·임시 글), chunk_no는 0부터 차례로
def test_split_node_sections_and_chunks(monkeypatch):
	install_ingest_store(monkeypatch, FakeIngestStore())
	table, chart = make_table(page=3), make_table(page=5, source_type="chart", caption="[그림 1]")

	result = split_node({"pages": PAGES, "tables": [table, chart]})

	assert [(s.section_no, s.title, s.start_page, s.end_page) for s in result["sections"]] == [
		(0, "1. 개요", 1, 1), (1, "2. 경기 동향", 2, 3), (2, "3. 전망", 5, 5),
	]
	chunks = result["chunks"]
	assert [c.chunk_no for c in chunks] == list(range(len(chunks)))
	text_chunks = [c for c in chunks if c.chunk_type == "text"]
	assert [c.section_no for c in text_chunks] == [0, 1, 2]
	assert all(c.doc_id is None and c.report_id is None for c in text_chunks)
	assert [(c.chunk_type, c.section_no, c.page, c.doc_id, c.text) for c in chunks[-2:]] == [
		("table", 1, 3, table.doc_id, PENDING_TABLE_TEXT),
		("chart", 2, 5, chart.doc_id, PENDING_TABLE_TEXT),
	]
	assert "warnings" not in result


# [확인 2] CHUNK_SIZE·CHUNK_OVERLAP 설정값으로 텍스트를 자른다 (chunk_size를 작게 하면 청크가 늘어남)
def test_split_node_uses_chunk_settings(monkeypatch):
	install_ingest_store(monkeypatch, FakeIngestStore(), chunk_size=20, chunk_overlap=5)
	result = split_node({"pages": PAGES, "tables": []})
	assert len(result["chunks"]) > 3
	assert all(len(c.text) <= 20 for c in result["chunks"])


# [확인 3] 섹션 범위 밖 쪽(4쪽)의 표는 그 앞에서 시작한 마지막 섹션에 붙는다
def test_split_node_table_outside_sections(monkeypatch):
	install_ingest_store(monkeypatch, FakeIngestStore())
	result = split_node({"pages": PAGES, "tables": [make_table(page=4)]})
	assert result["chunks"][-1].section_no == 1


# [확인 4] 섹션이 하나도 없으면(예: 모두 스캔본) 표·차트 청크를 만들지 않고 경고를 남긴다
def test_split_node_without_sections(monkeypatch):
	install_ingest_store(monkeypatch, FakeIngestStore())
	result = split_node({"pages": [Page(page=1, text="", is_scanned=True)], "tables": [make_table(page=1)]})
	assert result["sections"] == [] and result["chunks"] == []
	assert result["warnings"] == [NO_SECTION_WARNING]


# ─────────────────────────────────────────────────────────────
# 2. summarize_tables_node
# ─────────────────────────────────────────────────────────────

# [확인 5] 표·차트 청크의 글을 '캡션\n요약'으로 바꾸고, 반환 키는 summarized_tables·chunks ('tables'로 돌려주지 않음)
def test_summarize_tables_node(monkeypatch):
	install_ingest_store(monkeypatch, FakeIngestStore())
	table, chart = make_table(page=3), make_table(page=5, source_type="chart", caption=None)
	state = split_node({"pages": PAGES, "tables": [table, chart]})
	state["tables"] = [table, chart]

	def fake_summarize(tables):
		return [t.model_copy(update={"summary": f"{t.page}쪽 요약"}) for t in tables]

	monkeypatch.setattr(table_loader, "summarize_tables", fake_summarize, raising=False)
	result = summarize_tables_node(state)

	assert set(result) == {"summarized_tables", "chunks"}
	assert [t.summary for t in result["summarized_tables"]] == ["3쪽 요약", "5쪽 요약"]
	by_type = {c.chunk_type: c.text for c in result["chunks"] if c.chunk_type != "text"}
	assert by_type == {"table": "< 주요 지표 >\n3쪽 요약", "chart": "5쪽 요약"}
	texts_before = [c.text for c in state["chunks"] if c.chunk_type == "text"]
	assert [c.text for c in result["chunks"] if c.chunk_type == "text"] == texts_before


# [확인 6] 표·차트가 0개면 summarize_tables(LLM)를 부르지 않고 그대로 통과한다
def test_summarize_tables_node_without_tables(monkeypatch):
	def must_not_call(tables):
		raise AssertionError("표가 없는데 summarize_tables를 불렀습니다")

	monkeypatch.setattr(table_loader, "summarize_tables", must_not_call, raising=False)
	assert summarize_tables_node({"tables": [], "chunks": []}) == {"summarized_tables": []}


# ─────────────────────────────────────────────────────────────
# 3. persist_node
# ─────────────────────────────────────────────────────────────

def persist_state():
	sections = [Section(section_no=0, title="1. 개요", start_page=1, end_page=2, text="1. 개요\n본문")]
	table = make_table(page=2).model_copy(update={"summary": "요약"})
	chunks = [
		Chunk(chunk_no=0, section_no=0, page=1, chunk_type="text", text="본문"),
		Chunk(chunk_no=1, section_no=0, page=2, chunk_type="table", text="< 주요 지표 >\n요약", doc_id=table.doc_id),
	]
	return {
		"file_path": "data/raw/report.pdf", "user_id": "tester",
		"meta": {"title": "2026년 경제전망", "publisher": "현대경제연구원", "published_date": date(2026, 7, 31)},
		"pages": [Page(page=3, text="..."), Page(page=12, text="...")],
		"sections": sections, "summarized_tables": [table], "chunks": chunks,
	}


# [확인 7] 정상 저장: 한 연결로 insert_report → insert_sections → insert_tables → insert_chunks → add_chunks → commit → close
def test_persist_node_success(monkeypatch):
	store = install_ingest_store(monkeypatch, FakeIngestStore(report_id=7))
	state = persist_state()

	result = persist_node(state)

	assert store.events == ["get_connection", "insert_report", "insert_sections", "insert_tables", "insert_chunks", "add_chunks", "commit", "close"]
	conn = store.connections[0]
	assert all(store.calls[name][0] is conn for name in ("insert_report", "insert_sections", "insert_tables", "insert_chunks"))
	report = store.calls["insert_report"][1]
	assert isinstance(report, Report)
	assert (report.title, report.publisher, report.published_date, report.page_count, report.file_path, report.uploaded_by) == (
		"2026년 경제전망", "현대경제연구원", date(2026, 7, 31), 12, "data/raw/report.pdf", "tester")
	assert store.calls["insert_tables"][2] == state["summarized_tables"]
	assert [c.report_id for c in store.calls["add_chunks"][0]] == [7, 7]                # Chroma에는 report_id를 채운 청크
	assert result == {"report_id": 7, "counts": {"text": 1, "table": 1, "chart": 0}}


# [확인 8] 중간 실패(rollback): 어느 단계에서 실패해도 rollback → delete_report_vectors(report_id) → 원래 예외를 다시 던진다
#          commit은 실패한 경우가 아니면 부르지 않는다
@pytest.mark.parametrize("fail_at", ["insert_sections", "insert_tables", "insert_chunks", "add_chunks", "commit"])
def test_persist_node_rollback(monkeypatch, fail_at):
	store = install_ingest_store(monkeypatch, FakeIngestStore(report_id=7, fail_at=fail_at))

	with pytest.raises(RuntimeError, match=f"{fail_at} 실패"):
		persist_node(persist_state())

	assert store.events[-3:] == ["rollback", "delete_report_vectors", "close"]
	assert store.calls["delete_report_vectors"] == (7,)
	if fail_at != "commit":
		assert "commit" not in store.events


# [확인 9] insert_report에서 실패하면 report_id가 없으므로 rollback만 하고 벡터 정리는 하지 않는다
def test_persist_node_rollback_before_report_id(monkeypatch):
	store = install_ingest_store(monkeypatch, FakeIngestStore(fail_at="insert_report"))
	with pytest.raises(RuntimeError, match="insert_report 실패"):
		persist_node(persist_state())
	assert store.events == ["get_connection", "insert_report", "rollback", "close"]


# [확인 10] 벡터 정리(delete_report_vectors)까지 실패해도 원래 오류를 그대로 던지고, 정리 실패는 로그로 남긴다
def test_persist_node_cleanup_failure_keeps_original_error(monkeypatch, caplog):
	store = FakeIngestStore(report_id=7, fail_at="add_chunks")
	install_ingest_store(monkeypatch, store)

	def failing_delete(report_id):
		store.events.append("delete_report_vectors")
		raise ConnectionError("Chroma 연결 끊김")

	import hri.vector_store.chroma_store as chroma_store
	monkeypatch.setattr(chroma_store, "delete_report_vectors", failing_delete)

	with pytest.raises(RuntimeError, match="add_chunks 실패"):
		persist_node(persist_state())
	assert "보고서 7 벡터 정리 실패" in caplog.text


# ─────────────────────────────────────────────────────────────
# 4. 추출 노드 (3.8.2)
# ─────────────────────────────────────────────────────────────

# HRI 보고서 모양을 줄인 7쪽짜리 테스트용 보고서의 쪽별 글
#   1쪽 표지 / 2쪽 목차 / 3~5·7쪽 본문(1줄 보고서 제목 머리말, 2줄 쪽 번호) / 6쪽 빈 쪽(스캔본 흉내)
SAMPLE_TITLE = "물가 안정, 이제는 장기전으로"
SAMPLE_PAGES = [
	f"{SAMPLE_TITLE}\n- 2026년 상반기 물가 평가 및 하반기 전망\n현대경제연구원\n2026.07.01",
	"목 차\n1. 개요··················· 1\n2. 물가 동향··················· 2\n3. 전망··················· 4",
	f"{SAMPLE_TITLE}\n1\n1. 개요\n올해 상반기 소비자물가는 2%대 초반에서 안정되었다.\n다만 생활물가는 여전히 높다.",
	f"{SAMPLE_TITLE}\n2\n2. 물가 동향\n농산물 가격이 크게 올랐다.\n석유류 가격은 내려갔다.",
	f"{SAMPLE_TITLE}\n3\n서비스 물가는 오름세가 이어졌다.\n외식 가격이 특히 많이 올랐다.",
	"",
	f"{SAMPLE_TITLE}\n4\n3. 전망\n하반기 물가는 2% 안팎에서 움직일 것으로 보인다.\n장기적인 관리가 필요하다.",
]


@pytest.fixture
def sample_report(tmp_path):
	"""테스트용 보고서 PDF(7쪽)를 pymupdf로 만들어 경로를 돌려준다 (fontname='korea': pymupdf에 들어 있는 한글 글꼴)."""
	doc = pymupdf.open()
	for text in SAMPLE_PAGES:
		page = doc.new_page(width=595, height=842)
		if text:
			page.insert_text((72, 72), text, fontname="korea")
	path = tmp_path / "sample_report.pdf"
	doc.save(str(path))
	doc.close()
	return str(path)


def raw_table(page, caption, body_rows, blank=False):
	"""extract_tables()가 돌려줄 모양의 표: 머리글 1행 + 본문 body_rows행 (blank=True면 첫 칸 하나를 비움)."""
	rows = [["연도", "물가상승률"]] + [[str(2000 + i), f"{i}.0"] for i in range(body_rows)]
	if blank:
		rows[1][1] = ""
	return Table(page=page, caption=caption, rows=rows, markdown="(추출 직후)")


def make_charts(count):
	return [ChartImage(page=3 + i, caption=f"[그림 {i + 1}]", bbox=(0, 0, 200, 200), image_path=f"chart_{i + 1}.png")
		for i in range(count)]


# [확인 11] load_text: 표지·목차를 잘라 낸 본문 쪽(3~7쪽), 머리말·쪽 번호 제거, 빈 쪽(6쪽) 경고,
#           extract_report_meta에는 표지를 자르기 전(1~7쪽) 정제 전 pages를 넘긴다 (표지 제목 줄이 남아 있어야 함)
def test_load_text_node(monkeypatch, sample_report):
	fake = install_extract_stubs(monkeypatch, FakeExtractors())

	result = load_text_node({"file_path": sample_report})

	assert set(result) == {"pages", "meta", "warnings"}
	assert [p.page for p in result["pages"]] == [3, 4, 5, 6, 7]
	assert result["pages"][0].text.startswith("1. 개요")
	assert all(SAMPLE_TITLE not in p.text for p in result["pages"])
	assert result["warnings"] == [SCANNED_PAGE_WARNING.format(page=6)]
	assert result["meta"] == fake.meta
	assert fake.calls == [("extract_report_meta", sample_report, [1, 2, 3, 4, 5, 6, 7])]
	assert fake.meta_pages[0].text.startswith(SAMPLE_TITLE)


# [확인 12] extract_tables: 검수 경고는 warnings에, 큰 표(23행)는 머리글을 붙인 3조각으로 나눠
#           캡션에 '(1/3)'을 붙이고 조각마다 새 doc_id, 작은 표는 캡션 그대로 1개
def test_extract_tables_node(monkeypatch):
	small = raw_table(page=3, caption="[표 1]", body_rows=3, blank=True)
	big = raw_table(page=4, caption="[표 2]", body_rows=23)
	install_extract_stubs(monkeypatch, FakeExtractors(tables=[small, big]))

	result = extract_tables_node({"file_path": "report.pdf"})
	tables = result["tables"]

	assert [t.caption for t in tables] == ["[표 1]", "[표 2] (1/3)", "[표 2] (2/3)", "[표 2] (3/3)"]
	assert [t.page for t in tables] == [3, 4, 4, 4]
	assert [len(t.rows) for t in tables] == [4, 11, 11, 4]
	assert all(t.rows[0] == ["연도", "물가상승률"] for t in tables)
	assert all(t.source_type == "table" and t.markdown.startswith("| 연도 | 물가상승률 |") for t in tables)
	assert len({t.doc_id for t in tables}) == 4
	assert result["warnings"] == [TABLE_WARNING.format(page=3, caption="[표 1]", result="⚠️ 빈 칸 1개")]


# [확인 13] 표가 0개면 빈 목록 두 개
def test_extract_tables_node_without_tables(monkeypatch):
	install_extract_stubs(monkeypatch, FakeExtractors())
	assert extract_tables_node({"file_path": "report.pdf"}) == {"tables": [], "warnings": []}


# [확인 14] extract_charts: extract_charts(file_path) 결과를 charts 키로
def test_extract_charts_node(monkeypatch):
	charts = make_charts(2)
	install_extract_stubs(monkeypatch, FakeExtractors(charts=charts))
	assert extract_charts_node({"file_path": "report.pdf"}) == {"charts": charts}


# [확인 15] process_charts: 차트가 0개면 OCR·Vision을 부르지 않고 바로 빈 결과
@pytest.mark.parametrize("state", [{}, {"charts": []}])
def test_process_charts_node_without_charts(monkeypatch, state):
	fake = install_extract_stubs(monkeypatch, FakeExtractors())
	assert process_charts_node(state, {}) == {"tables": [], "warnings": []}
	assert fake.ocr_order == [] and fake.peak == 0


# [확인 16] process_charts: OCR은 차트 순서대로 하나씩, 실패한 차트(OCR 실패·Vision 실패·예상 밖 오류)는
#           str(예외)를 warnings에 남기고 건너뜀. 성공한 차트 표는 차트 순서대로 tables에, 예상 밖 오류는 로그도 남김
def test_process_charts_node_skips_failed_charts(monkeypatch, caplog):
	charts = make_charts(5)
	fake = install_extract_stubs(monkeypatch, FakeExtractors(
		charts=charts, ocr_fail={"chart_2.png"}, vision_fail={"chart_3.png"}, vision_crash={"chart_5.png"},
	))

	with caplog.at_level("ERROR"):
		result = process_charts_node({"charts": charts}, {})

	assert fake.ocr_order == [c.image_path for c in charts]
	assert [(t.caption, t.source_type) for t in result["tables"]] == [("[그림 1]", "chart"), ("[그림 4]", "chart")]
	assert result["warnings"] == [
		"chart_2.png: OCR 실패",
		"5쪽 차트: Vision 응답을 ChartData로 바꾸지 못함",
		"7쪽 차트: 예상 밖 오류",
	]
	assert "7쪽 차트 처리 중 예상 밖 오류" in caplog.text
	assert "5쪽" not in caplog.text  # ChartExtractError는 예상한 실패라 로그 대신 경고만


# [확인 17] process_charts: Vision 단계는 동시에 최대 CHART_MAX_CONCURRENCY(4)개까지만 실행된다
#           (차트 8개, 1개에 0.1초 → 4개씩 겹쳐 실행되고 5개 이상 겹치지 않음)
def test_process_charts_node_max_concurrency(monkeypatch):
	charts = make_charts(8)
	fake = install_extract_stubs(monkeypatch, FakeExtractors(charts=charts, vision_delay=0.1))

	result = process_charts_node({"charts": charts}, {})

	assert len(result["tables"]) == 8
	assert fake.peak == CHART_MAX_CONCURRENCY == 4


def install_fake_validate(monkeypatch):
	"""validate 노드가 부르는 document_service.validate_pdf를 '통과'로 바꿔 끼운다.

	진짜 document_service는 import할 때 llm_client(.env 필요)를 불러온다. 진짜 validate_pdf로 확인하는
	업로드 테스트는 tests/test_graphs.py 확인 10·11에 있으므로, 여기서는 추출·분할 흐름만 본다.
	"""
	module = types.ModuleType("hri.service.document_service")
	module.validate_pdf = lambda file_path: ValidationResult(ok=True)
	monkeypatch.setitem(sys.modules, "hri.service.document_service", module)


# [확인 18] 전체 업로드 1회 실행 (sample_report): 진짜 load_pdf·clean_text·find_body_start·split_sections·split_text,
#           가짜 추출 함수(표 2개 중 1개는 큰 표, 차트 3개 중 1개 실패)·DB·Chroma
#   - 세 갈래(텍스트·표·차트)가 돌려준 tables·warnings가 operator.add로 합쳐져 split에 들어간다
#   - persist의 counts와 실제 저장된 청크 수가 같다 (청크 수를 화면에 출력: pytest -s)
def test_ingest_upload_once_with_sample_report(monkeypatch, sample_report):
	install_fake_validate(monkeypatch)
	store = install_ingest_store(monkeypatch, FakeIngestStore())
	charts = make_charts(3)
	install_extract_stubs(monkeypatch, FakeExtractors(
		tables=[raw_table(page=3, caption="[표 1]", body_rows=3), raw_table(page=4, caption="[표 2]", body_rows=15)],
		charts=charts, vision_fail={"chart_2.png"},
	))

	inputs = {"file_path": sample_report, "user_id": "tester"}
	updates = list(registry.get_ingest_graph().stream(inputs, stream_mode="updates"))
	nodes = [name for update in updates for name in update]
	final = registry.get_ingest_graph().invoke(inputs)

	# 노드 순서: validate → 세 갈래(순서 무관) → process_charts → split(1번) → summarize_tables → persist
	assert nodes[0] == "validate"
	assert set(nodes[1:4]) == {"load_text", "extract_tables", "extract_charts"}
	assert nodes[4:] == ["process_charts", "split", "summarize_tables", "persist"]

	# 세 갈래 결과가 합쳐짐: 표 3개(작은 표 1 + 큰 표 2조각) + 차트 표 2개, 경고는 빈 쪽 1 + 차트 실패 1
	assert [t.source_type for t in final["tables"]] == ["table", "table", "table", "chart", "chart"]
	assert sorted(final["warnings"]) == sorted([SCANNED_PAGE_WARNING.format(page=6), "4쪽 차트: Vision 응답을 ChartData로 바꾸지 못함"])

	# 저장: 섹션 3개(1. 개요 · 2. 물가 동향 · 3. 전망), 청크 = 텍스트 + 표 3 + 차트 2
	sections, chunks = store.calls["insert_sections"][2], store.calls["insert_chunks"][2]
	assert [s.title for s in sections] == ["1. 개요", "2. 물가 동향", "3. 전망"]
	counts = final["counts"]
	assert counts["table"] == 3 and counts["chart"] == 2 and counts["text"] >= 3
	assert sum(counts.values()) == len(chunks) == len(final["chunks"])
	assert store.calls["insert_report"][1].title == "테스트 보고서"
	print(f"\n[업로드 1회 실행] report_id={final['report_id']} 섹션 {len(sections)}개, 청크 {len(chunks)}개 {counts}, 경고 {final['warnings']}")
