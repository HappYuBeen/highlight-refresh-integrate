"""보고서 업로드 흐름 · build_ingest_graph, validate_node, route_after_validate, load_text_node, extract_tables_node, extract_charts_node, process_charts_node, split_node, summarize_tables_node, persist_node (엄정현)

[3.8.1] 그래프 구성(build_ingest_graph)·검증 노드(validate_node)·검증 분기(route_after_validate)를 명세대로 채웠다.
[3.8.3] 분할(split_node)·표 요약(summarize_tables_node)·저장(persist_node) 노드를 명세대로 채웠다.
	- split: 본문을 섹션(부모)과 검색용 청크(자식)로 나누고, 표·차트도 청크로 만든다
	- summarize_tables: 표·차트마다 1~2문장 요약을 만들어 표·차트 청크의 검색용 글(캡션+요약)로 넣는다
	- persist: DB(MySQL)와 벡터DB(Chroma)에 한 번에 저장한다. 중간에 실패하면 모두 되돌린다(rollback)
[3.8.2] 추출 노드(load_text·extract_tables·extract_charts·process_charts)를 명세대로 채웠다.
	- load_text: PDF에서 글자를 쪽마다 뽑아 정리하고(머리말·꼬리말·목차 제거), 제목·발행기관·발행일을 찾는다
	- extract_tables: PDF의 표를 행·열 그대로 뽑아 검수하고, 큰 표는 나눠 마크다운 표로 만든다
	- extract_charts: PDF의 차트 영역을 PNG 그림으로 잘라 낸다
	- process_charts: 차트 그림을 글자 인식(OCR) + Vision LLM으로 표 데이터로 바꾼다 (실패한 차트는 경고만 남기고 건너뜀)
모든 노드는 state 전체가 아니라 '바뀐 키만' dict로 돌려준다. tables·warnings는 operator.add 리듀서라
여러 노드가 동시에 돌려준 목록이 하나로 합쳐진다 (예: 표 노드의 표 + 차트 노드의 차트 표).

흐름 (LangGraph 워크플로우 명세서 build_ingest_graph 행)
	START → validate → route_after_validate
		├─ 실패(state['error']) → END
		└─ 통과 → [load_text ∥ extract_tables ∥ extract_charts] (같은 단계에서 병렬 실행)
	extract_charts → process_charts
	[load_text, extract_tables, process_charts] 세 갈래가 모두 끝나면 → split → summarize_tables → persist → END
compile은 registry.get_ingest_graph()가 한 번만 한다.
"""
import logging
from collections import Counter
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.graph import END, START, StateGraph
from hri.domain.schemas import ChartImage, Chunk, IngestState, OcrToken, Report, Section, Table
from hri.dao.report_dao import insert_chunks, insert_report, insert_sections, insert_tables, make_file_hash

logger = logging.getLogger(__name__)

# 검증을 통과하면 동시에 시작하는 추출 노드 3개
PARALLEL_EXTRACT = ["load_text", "extract_tables", "extract_charts"]

# TODO(확인 필요): validate_pdf()가 ok=False인데 reason을 비워 돌려줄 때 화면에 보여 줄 문구.
#   명세서에는 'reason을 state['error']에 기록'만 있고, reason이 없을 때의 문구는 정해져 있지 않다.
#   지금 validate_pdf()는 실패할 때 항상 reason을 채우므로 대비용이다. 문구가 정해지면 이 값만 바꾼다.
VALIDATE_FAILED_REASON = "PDF 파일을 확인할 수 없습니다"


def validate_node(state: IngestState) -> dict:
	"""업로드한 PDF를 validate_pdf()로 검사한다 (확장자·용량·열림·진짜 PDF·암호·쪽 수).

	- 실패(ok=False): {'error': reason} → route_after_validate가 END로 보내 그래프를 끝낸다.
	- 통과(ok=True): {'error': None} (IngestState.error는 '정상이면 None')
	  빈 dict 대신 error=None을 돌려주면 stream(stream_mode='updates')에서 {'validate': {'error': None}}으로 와서
	  upload_report()가 검증 결과를 None이 아닌 dict로 받을 수 있다.

	validate_pdf는 함수 안에서 import한다: document_service가 hri.graph.registry를 import하므로
	파일 맨 위에서 import하면 graph ↔ service 순환 import가 생긴다.
	"""
	from hri.service.document_service import validate_pdf

	result = validate_pdf(state["file_path"])
	if not result.ok:
		return {"error": result.reason or VALIDATE_FAILED_REASON}
	return {"error": None}


# ── 추출 노드에서 쓰는 값 ─────────────────────────────────────────────────────────
# TODO(확인 필요): 스캔본·빈 쪽 경고 문구. 명세서에는 'is_scanned 페이지 경고는 warnings에 기록'만 있고 문구는 없다.
SCANNED_PAGE_WARNING = "{page}쪽은 글자가 없는 쪽(스캔본·빈 쪽)이라 내용을 읽지 못했습니다"

# validate_table()이 이상이 없을 때 돌려주는 글의 시작 (명세서 validate_table 행: "'✅ 이상 없음' 또는 경고를 반환")
TABLE_OK_MARK = "✅"

# TODO(확인 필요): 표 검수 경고 문구. 명세서에는 '경고는 warnings'만 있어서, 어느 쪽 어느 표인지 앞에 붙인다.
TABLE_WARNING = "{page}쪽 표 {caption}: {result}"

# Vision 호출을 동시에 몇 개까지 보낼지 (명세서 process_charts_node 행: config={'max_concurrency': 4})
CHART_MAX_CONCURRENCY = 4


def load_text_node(state: IngestState) -> dict:
	"""[텍스트 노드] PDF의 글자를 쪽마다 뽑아 정리하고, 보고서 정보(제목 등)를 찾는다.

	순서 (명세서 load_text_node 행, 함수는 모두 엄정현 pdf_loader)
	1. load_pdf(file_path)     : 쪽마다 글자를 뽑는다. 글자가 없는 쪽은 is_scanned=True로 표시된다
	2. clean_text(pages)       : 여러 쪽에 반복되는 머리말·꼬리말, 쪽 번호, 깨진 글자를 지운다
	3. extract_report_meta(file_path, 정제 전 pages) : 제목·발행기관·발행일 dict를 만든다 (persist가 reports에 저장)
	   - clean_text()가 표지 제목 줄도 머리말로 지우므로, 정제 전 load_pdf() 결과를 넘긴다
	4. find_body_start(pages)  : 표지·목차를 잘라 내고 본문 시작 쪽부터 남긴다 → split 노드의 입력
	5. 스캔본·빈 쪽마다 경고 1줄을 warnings에 넣는다 (업로드 결과 화면에 표시)

	돌려주는 값: {'pages': 본문 쪽 목록, 'meta': {title, publisher, published_date}, 'warnings': [...]}
	함수는 함수 안에서 import한다 (그래프 파일을 import할 때 PDF 라이브러리를 불러오지 않으려고).
	"""
	from hri.loader.pdf_loader import clean_text, extract_report_meta, find_body_start, load_pdf

	file_path = state["file_path"]
	raw_pages = load_pdf(file_path)
	cleaned = clean_text(raw_pages)
	meta = extract_report_meta(file_path, raw_pages)
	body_pages = find_body_start(cleaned)

	warnings = [SCANNED_PAGE_WARNING.format(page=page.page) for page in raw_pages if page.is_scanned]
	return {"pages": body_pages, "meta": meta, "warnings": warnings}


def _piece_caption(caption: str | None, index: int, total: int) -> str | None:
	"""큰 표를 나눈 조각의 캡션. 나눴으면 '(1/3)'처럼 몇 번째 조각인지 붙인다 (Table.caption 설명: '(1/n)'을 붙임)."""
	if total == 1:
		return caption
	mark = f"({index}/{total})"
	return f"{caption} {mark}" if caption else mark


def extract_tables_node(state: IngestState) -> dict:
	"""[표 노드] PDF의 표를 뽑아 검수하고, 마크다운 표(Table)로 만들어 {'tables', 'warnings'}로 돌려준다.

	순서 (명세서 extract_tables_node 행, 함수는 모두 엄정현 table_loader)
	1. extract_tables(file_path)          : pdfplumber로 표를 행·열 그대로(rows) 뽑는다. 쪽 번호·캡션('[표 n]')도 함께
	2. validate_table(rows)               : 빈 칸·병합 의심 칸을 찾아 '✅ 이상 없음' 또는 경고 글을 돌려준다
	   - '✅'로 시작하지 않으면 경고로 보고 warnings에 '쪽·캡션·경고'를 넣는다 (표는 그대로 저장)
	3. split_big_table(rows)              : 행이 많은 표(기본 10행 초과)를 머리글을 붙인 여러 조각으로 나눈다
	4. 조각마다 table_to_markdown(rows)   : 마크다운 표 글로 바꾸고 Table 1개를 만든다
	   - 조각이 여러 개면 캡션에 '(1/n)'을 붙이고, 조각마다 새 Table을 만들어 doc_id(원본 표 번호)가 겹치지 않게 한다
	     (report.py Table 설명: 'model_copy()로 만들면 doc_id가 복사되므로 새로 만들 것')

	돌려주는 값: {'tables': [Table, ...], 'warnings': [...]}
	tables·warnings는 operator.add 리듀서라 차트 노드가 돌려준 차트 표·경고와 합쳐진다.
	"""
	from hri.loader.table_loader import extract_tables, split_big_table, table_to_markdown, validate_table

	tables: list[Table] = []
	warnings: list[str] = []

	for table in extract_tables(state["file_path"]):
		# 2) 검수: 이상이 있으면 경고만 남기고 표는 계속 처리한다
		result = validate_table(table.rows)
		if not result.startswith(TABLE_OK_MARK):
			warnings.append(TABLE_WARNING.format(page=table.page, caption=table.caption or "(캡션 없음)", result=result))

		# 3)·4) 나누고 조각마다 마크다운 표 1개
		pieces = split_big_table(table.rows)
		for index, rows in enumerate(pieces, start=1):
			tables.append(Table(
				page=table.page,
				caption=_piece_caption(table.caption, index, len(pieces)),
				rows=rows,
				markdown=table_to_markdown(rows),
				source_type="table",
			))

	return {"tables": tables, "warnings": warnings}


def extract_charts_node(state: IngestState) -> dict:
	"""[차트 자르기 노드] PDF에서 차트 영역을 찾아 PNG 그림으로 잘라 {'charts': [ChartImage, ...]}로 돌려준다.

	extract_charts(file_path) (엄정현 chart_loader): 그림·도형 영역 중 작은 것(120pt 미만)과 반복 로고를 빼고,
	캡션('[그림 n]')을 찾아 data/charts/raw/에 PNG로 저장한다.
	명세서는 'extract_charts 노드가 state['charts']에 넣은 차트'라고만 하고 함수 이름이 없어
	다른 노드처럼 '*_node'로 이름을 붙였다. 3.8.2 목록에는 없지만 process_charts의 입력을 만드는 노드라 함께 연결했다.
	"""
	from hri.loader.chart_loader import extract_charts

	return {"charts": extract_charts(state["file_path"])}


def _chart_to_table(item: tuple[ChartImage, list[OcrToken]]) -> Table:
	"""차트 1개를 표(Table)로 바꾼다: chart_to_table → validate_chart → chart_to_markdown (엄정현 chart_loader).

	- chart_to_table(chart, tokens) : Vision LLM이 차트 그림과 OCR 글자를 보고 표 데이터(ChartData)로 복원한다.
	  실패하면 ChartExtractError를 낸다.
	- validate_chart(data, tokens)  : 값 개수·OCR 글자와 맞는 비율로 신뢰도를 매기고, 의심되면 needs_review=True
	- chart_to_markdown(data, chart): Table(source_type='chart')로 바꾼다 → 이후 표와 같은 길로 요약·저장된다
	process_charts_node가 RunnableLambda로 감싸 여러 차트를 동시에 처리한다.
	"""
	from hri.loader.chart_loader import chart_to_markdown, chart_to_table, validate_chart

	chart, tokens = item
	data = validate_chart(chart_to_table(chart, tokens), tokens)
	return chart_to_markdown(data, chart)


def process_charts_node(state: IngestState, config: RunnableConfig) -> dict:
	"""[차트 노드] 잘라 낸 차트 그림을 표(Table)로 바꿔 {'tables', 'warnings'}로 돌려준다.

	순서 (명세서 process_charts_node 행)
	0. 차트가 0개면 아무것도 하지 않고 바로 빈 결과를 돌려준다
	1. ocr_chart(image_path) 를 차트마다 **하나씩 차례로** 실행해 그림 속 글자(OcrToken)를 읽는다 (엄정현 chart_loader)
	   - EasyOCR은 한 번 만든 Reader를 함께 쓰고 CPU 계산이라, 동시에 돌려도 빨라지지 않고 충돌할 수 있어서 순서대로 한다
	2. chart_to_table → validate_chart → chart_to_markdown 을 RunnableLambda 하나로 묶어
	   .batch(..., config={'max_concurrency': 4}, return_exceptions=True) 로 **동시에 최대 4개씩** 처리한다
	   - 오래 걸리는 Vision LLM 호출만 병렬로 돌려 시간을 줄인다
	   - return_exceptions=True: 차트 하나가 실패해도 전체가 멈추지 않고, 그 자리에 예외가 결과로 들어온다
	3. 실패한 차트(OCR 실패 포함)는 오류 내용(ChartExtractError 메시지)을 warnings에 넣고 건너뛴다
	   - ChartExtractError가 아닌 예상 밖 오류도 건너뛰되, 원인 확인을 위해 로그를 남긴다
	4. 성공한 차트 표는 {'tables': [...]}로 돌려줘 표 노드의 표와 합쳐진다 (operator.add)

	config는 LangGraph가 넘겨주는 실행 설정이다. batch에 그대로 이어 주어
	업로드 사용량 집계(callbacks)가 Vision 호출에도 적용되게 한다.
	"""
	charts: list[ChartImage] = state.get("charts") or []
	if not charts:
		return {"tables": [], "warnings": []}

	from hri.exception.errors import ChartExtractError
	from hri.loader.chart_loader import ocr_chart

	warnings: list[str] = []

	# 1) OCR은 차례로 (실패한 차트는 경고만 남기고 다음 단계에서 뺀다)
	items: list[tuple[ChartImage, list[OcrToken]]] = []
	for chart in charts:
		try:
			items.append((chart, ocr_chart(chart.image_path)))
		except ChartExtractError as error:
			warnings.append(str(error))

	# 2) Vision 구조화·검수·표 변환은 동시에 최대 4개
	results = RunnableLambda(_chart_to_table).batch(
		items,
		config={**config, "max_concurrency": CHART_MAX_CONCURRENCY},
		return_exceptions=True,
	)

	# 3)·4) 성공한 표만 모으고, 실패는 경고로
	tables: list[Table] = []
	for (chart, _tokens), result in zip(items, results):
		if isinstance(result, Exception):
			if not isinstance(result, ChartExtractError):
				logger.error("%s쪽 차트 처리 중 예상 밖 오류", chart.page, exc_info=result)
			warnings.append(str(result))
			continue
		tables.append(result)

	return {"tables": tables, "warnings": warnings}


# ── 명세서에 정해지지 않은 값 (정해지면 아래 값만 바꾼다) ─────────────────────────────
# TODO(확인 필요): split 단계에서 표·차트 청크에 잠깐 넣어 두는 글.
#   명세서는 '표·차트 청크의 text는 summarize_tables에서 채움'인데, Chunk.text는 빈 글을 받지 않는다(min_length=1).
#   그래서 summarize_tables 노드가 '캡션+요약'으로 바꾸기 전까지 이 글을 넣어 둔다 (DB·벡터DB에는 저장되지 않음).
PENDING_TABLE_TEXT = "(요약 전)"

# TODO(확인 필요): 섹션을 하나도 찾지 못했을 때(예: 모든 쪽이 스캔본) 남기는 경고.
#   표·차트 청크는 섹션 번호(sections FK)가 있어야 저장할 수 있으므로 이 경우 표·차트 청크를 만들지 않는다.
NO_SECTION_WARNING = "본문 섹션을 찾지 못해 표·차트를 검색 청크로 만들지 않았습니다"


def _section_no_for_page(sections: list[Section], page: int) -> int:
	"""표·차트가 있는 쪽(page)이 속한 섹션 번호를 찾는다 (명세서: 'page가 속한 섹션의 section_no').

	- start_page ≤ page ≤ end_page인 섹션을 찾는다.
	- TODO(확인 필요): 어느 섹션 범위에도 없는 쪽이면(섹션 사이의 빈 쪽 등) 그 쪽보다 앞에서 시작한 마지막 섹션,
	  그것도 없으면 첫 섹션으로 붙인다. 명세서에는 범위 밖일 때의 규칙이 없다.
	"""
	for section in sections:
		if section.start_page <= page <= section.end_page:
			return section.section_no
	before = [s for s in sections if s.start_page <= page]
	return (before[-1] if before else sections[0]).section_no


def split_node(state: IngestState) -> dict:
	"""[분할 노드] 정제한 쪽(pages)을 섹션과 검색용 청크로 나눈다. {'sections', 'chunks'}를 돌려준다.

	순서 (명세서 split_node 행)
	1. split_sections(pages)  : 목차 제목(Ⅰ./1./가.)을 기준으로 섹션(부모, 답변 근거 원문)을 나눈다 (김희진 text_splitter)
	2. 섹션마다 split_text(섹션 글, CHUNK_SIZE, CHUNK_OVERLAP) : 검색용 텍스트 청크(자식)로 자른다
	   - CHUNK_SIZE·CHUNK_OVERLAP은 .env 설정값(Settings.chunk_size·chunk_overlap, 기본 500·50)
	   - TODO(확인 필요): 텍스트 청크의 page(출처 쪽)는 섹션 시작 쪽으로 넣는다. 청크마다 정확한 쪽을 찾는 규칙은 명세서에 없다.
	3. 표·차트(state['tables'])마다 표·차트 청크를 1개씩 만든다
	   - section_no: 표가 있는 쪽이 속한 섹션 번호
	   - chunk_type: 'table' 또는 'chart' (Table.source_type), doc_id: Table.doc_id (report_tables 원본 표와 연결)
	   - text: 아직 요약이 없으므로 PENDING_TABLE_TEXT를 넣어 두고, summarize_tables 노드가 '캡션+요약'으로 바꾼다
	4. chunk_no는 보고서 안에서 0부터 차례로 붙인다 (텍스트 청크 → 표·차트 청크 순)
	"""
	from hri.config.settings import load_settings
	from hri.splitter.text_splitter import split_sections, split_text

	settings = load_settings()
	sections = split_sections(state.get("pages") or [])
	chunks: list[Chunk] = []

	# 2) 섹션별 텍스트 청크
	for section in sections:
		for piece in split_text(section.text, settings.chunk_size, settings.chunk_overlap):
			chunks.append(Chunk(chunk_no=len(chunks), section_no=section.section_no, page=section.start_page, chunk_type="text", text=piece))

	# 3) 표·차트 청크
	tables: list[Table] = state.get("tables") or []
	warnings: list[str] = []
	if tables and not sections:
		warnings.append(NO_SECTION_WARNING)
	elif tables:
		for table in tables:
			chunks.append(Chunk(
				chunk_no=len(chunks),
				section_no=_section_no_for_page(sections, table.page),
				page=table.page,
				chunk_type=table.source_type,
				text=PENDING_TABLE_TEXT,
				doc_id=table.doc_id,
			))

	update = {"sections": sections, "chunks": chunks}
	if warnings:
		update["warnings"] = warnings     # warnings는 operator.add 리듀서라 다른 노드 경고와 합쳐진다
	return update


# 요약에 실패한 표의 경고 (표 검수 경고 TABLE_WARNING과 같은 '쪽·캡션' 모양). message는 table_loader.SUMMARY_FAILED_MESSAGE
TABLE_SUMMARY_WARNING = "{page}쪽 표 {caption}: {message} (원본 표로 검색합니다)"


def _caption_and_summary(table: Table) -> str:
	"""표·차트 청크의 검색용 글을 만든다: '캡션(줄바꿈)요약' (명세서: '캡션+요약').

	캡션이 없으면 요약만 쓴다. 요약이 없으면(요약 실패) 원본 마크다운을 쓴다.
	(명세서에 규칙이 없어 3.8.3에서 넣은 대비 → 10/28 결정: 요약 실패는 그 표만 summary=None, 원본으로 검색)
	"""
	parts = [p for p in (table.caption, table.summary) if p]
	if not table.summary and table.markdown:
		parts.append(table.markdown)
	return "\n".join(parts) or PENDING_TABLE_TEXT


def summarize_tables_node(state: IngestState) -> dict:
	"""[표·차트 요약 노드] 표·차트마다 검색용 요약을 만들고, 표·차트 청크의 글을 '캡션+요약'으로 바꾼다.

	- summarize_tables(state['tables']) : Table.summary(1~2문장)를 채운 표 목록을 돌려준다 (엄정현 table_loader, LLM 사용)
	  DB 트랜잭션(persist) 밖에서 실행해, LLM이 느려도 DB 연결을 붙잡지 않게 한다 (명세서).
	- 표·차트 청크는 doc_id로 같은 표를 찾아 text를 '캡션+요약'으로 바꾼다. 텍스트 청크는 그대로 둔다.
	- 돌려주는 키는 'summarized_tables'와 'chunks'다.
	  'tables'로 돌려주면 operator.add 리듀서 때문에 같은 표가 두 번 쌓이므로 다른 키에 담는다.
	- 표·차트가 0개면 LLM을 부르지 않고 그대로 통과한다 ({'summarized_tables': []}).
	- 요약에 실패한 표(summary가 None)는 warnings에 TABLE_SUMMARY_WARNING을 넣어 업로드 결과 화면에 알린다 (10/28 결정).
	  실패가 없으면 warnings 키를 돌려주지 않는다 (split_node와 같음).
	"""
	tables: list[Table] = state.get("tables") or []
	if not tables:
		return {"summarized_tables": []}

	from hri.loader.table_loader import SUMMARY_FAILED_MESSAGE, summarize_tables

	summarized = summarize_tables(tables)
	by_doc_id = {table.doc_id: table for table in summarized}
	chunks = [
		chunk.model_copy(update={"text": _caption_and_summary(by_doc_id[chunk.doc_id])})
		if chunk.doc_id in by_doc_id else chunk
		for chunk in state.get("chunks") or []
	]
	update = {"summarized_tables": summarized, "chunks": chunks}
	warnings = [
		TABLE_SUMMARY_WARNING.format(page=t.page, caption=t.caption or "(캡션 없음)", message=SUMMARY_FAILED_MESSAGE)
		for t in summarized if not t.summary
	]
	if warnings:
		update["warnings"] = warnings     # warnings는 operator.add 리듀서라 다른 노드 경고와 합쳐진다
	return update


def _page_count(state: IngestState) -> int:
	"""reports.page_count(PDF 전체 쪽 수)에 넣을 값.

	TODO(확인 필요): 쪽 번호는 원본 PDF 기준(1부터)이고 find_body_start()가 앞쪽(표지·목차)만 빼므로
	가장 큰 쪽 번호를 전체 쪽 수로 쓴다. 전체 쪽 수를 따로 넘기는 키는 IngestState에 없다.
	"""
	pages = state.get("pages") or []
	return max((page.page for page in pages), default=0)


def persist_node(state: IngestState) -> dict:
	"""[저장 노드] 보고서·섹션·표·청크를 MySQL에, 청크 벡터를 Chroma에 한 번에 저장한다. {'report_id', 'counts'}를 돌려준다.

	순서 (명세서 persist_node 행, 하나의 DB 연결 = 하나의 트랜잭션)
	1. insert_report(conn, Report)            : reports에 1줄 → report_id (AUTO_INCREMENT)를 받는다 (김희진 report_dao)
	   - 제목·발행기관·발행일은 state['meta'](extract_report_meta 결과), 파일 경로·업로드한 사람은 입력값
	2. insert_sections(conn, report_id, sections)
	3. insert_tables(conn, report_id, summarized_tables)  : 표가 먼저 들어가야 청크의 doc_id(외래 키)가 맞는다 (엄정현 report_dao)
	4. insert_chunks(conn, report_id, chunks)
	5. add_chunks(chunks)                     : 청크를 임베딩해 Chroma에 저장 (김희진 chroma_store, report_id를 채운 청크)
	6. conn.commit()                          : 여기까지 모두 성공했을 때만 DB에 확정한다

	중간에 하나라도 실패하면 (명세서)
	- conn.rollback()으로 DB에 넣은 것을 모두 되돌리고
	- report_id를 이미 받았다면 delete_report_vectors(report_id)로 Chroma에 들어간 벡터도 지운다
	  (Chroma는 DB 트랜잭션에 묶이지 않아 rollback으로 지워지지 않음)
	- 그리고 원래 예외를 다시 던진다 → upload_report()가 잡아 화면에 '업로드 처리 중 오류'를 보여 준다
	LLM은 부르지 않는다.
	"""
	from hri.dao.db import get_connection
	from hri.dao.report_dao import insert_chunks, insert_report, insert_sections, insert_tables
	from hri.vector_store.chroma_store import add_chunks, delete_report_vectors

	meta = state.get("meta") or {}
	sections: list[Section] = state.get("sections") or []
	tables: list[Table] = state.get("summarized_tables") or []
	chunks: list[Chunk] = state.get("chunks") or []

	report = Report(
		title=meta["title"],
		publisher=meta.get("publisher"),
		published_date=meta.get("published_date"),
		page_count=_page_count(state),
		file_path=state["file_path"],
		uploaded_by=state["user_id"],
		file_hash=make_file_hash(state["file_path"], state["user_id"]),
	)

	report_id = None
	with get_connection() as conn:
		try:
			report_id = insert_report(conn, report)
			insert_sections(conn, report_id, sections)
			insert_tables(conn, report_id, tables)
			insert_chunks(conn, report_id, chunks)
			# Chroma 저장에는 report_id가 들어간 청크가 필요하다 (id = '{report_id}-{chunk_no}')
			add_chunks([chunk.model_copy(update={"report_id": report_id}) for chunk in chunks])
			conn.commit()
		except Exception:
			conn.rollback()
			if report_id is not None:
				try:
					delete_report_vectors(report_id)
				except Exception:
					# 벡터 정리까지 실패해도 원래 오류를 알리는 것이 우선이므로 기록만 남긴다
					logger.exception("보고서 %s 벡터 정리 실패 (Chroma에서 직접 지워야 함)", report_id)
			raise

	# 청크 종류별 개수 (업로드 결과 화면: 텍스트/표/차트 청크 수)
	kinds = Counter(chunk.chunk_type for chunk in chunks)
	counts = {"text": kinds["text"], "table": kinds["table"], "chart": kinds["chart"]}
	return {"report_id": report_id, "counts": counts}


def route_after_validate(state: IngestState) -> list[str] | str:
	"""validate 노드 다음 갈 곳을 고른다.

	state['error']가 있으면(검증 실패) END를 돌려줘 끝내고,
	없으면 추출 노드 3개 이름 리스트를 돌려줘 같은 단계(superstep)에서 병렬 실행한다.
	"""
	if state.get("error"):
		return END
	return PARALLEL_EXTRACT


def build_ingest_graph() -> StateGraph:
	"""업로드 그래프를 노드·엣지까지 연결해 돌려준다 (compile 전 StateGraph)."""
	graph = StateGraph(IngestState)

	# 노드 8개
	graph.add_node("validate", validate_node)
	graph.add_node("load_text", load_text_node)
	graph.add_node("extract_tables", extract_tables_node)
	graph.add_node("extract_charts", extract_charts_node)
	graph.add_node("process_charts", process_charts_node)
	graph.add_node("split", split_node)
	graph.add_node("summarize_tables", summarize_tables_node)
	graph.add_node("persist", persist_node)

	# 검증 → (실패면 END) / (통과면 추출 3개 병렬)
	graph.add_edge(START, "validate")
	graph.add_conditional_edges("validate", route_after_validate, [*PARALLEL_EXTRACT, END])

	# 차트는 잘라낸 뒤 표로 바꾸는 단계를 한 번 더 거친다
	graph.add_edge("extract_charts", "process_charts")

	# 세 갈래가 모두 끝나야 split 실행 (리스트로 묶은 엣지 = 모두 기다림)
	graph.add_edge(["load_text", "extract_tables", "process_charts"], "split")
	graph.add_edge("split", "summarize_tables")
	graph.add_edge("summarize_tables", "persist")
	graph.add_edge("persist", END)
	return graph