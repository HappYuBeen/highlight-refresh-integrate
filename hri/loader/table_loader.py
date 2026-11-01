"""표 추출·요약 · extract_tables, validate_table, table_to_markdown, split_big_table, summarize_tables (엄정현)"""

# 엄정현 table_to_markdown()·split_big_table() 추가 (WBS 3.2.7).
# - split_big_table()의 header_rows 인자(머리글 2줄 이상인 표)는 명세서에 없는 추가 기능이다 (함수 설명 참고).
# - extract_tables_node()(graph/ingest_graph)가 split_big_table()로 큰 표를 나누고, 조각마다 table_to_markdown()으로 마크다운 표를 만든다.
# - table_to_markdown()은 chart_to_markdown()(차트 → 표), build_compare_table()(지표 비교표), get_dashboard_data()(대시보드 표)에서도 쓴다.
# 엄정현 summarize_tables() 추가 (WBS 3.2.11).
# - summarize_tables_node()(graph/ingest_graph)가 split 뒤·persist 앞(DB 트랜잭션 밖)에서 부른다.
# 장유빈(엄정현 대신) extract_tables()·validate_table() 추가 (WBS 3.2.10).
# - extract_tables_node()(graph/ingest_graph)가 extract_tables()로 표를 뽑고, 조각마다 validate_table()로 검수해 경고를 warnings에 남긴다.
# - Table.rows에서 pdfplumber가 돌려준 값을 그대로 둔다: 칸이 비어 있으면 "", 병합된 칸 자리는 None (validate_table이 둘을 구분해 센다).

import logging
import re

from hri.domain.schemas import Table

logger = logging.getLogger(__name__)

# 셀 안의 줄바꿈·연속 공백. pdfplumber는 칸 안에서 줄이 바뀐 곳에 '\n'을 넣는다
# 예) HRI 보고서 표 칸 '‣ … 경제\n및 사회 환경의 급변은 …' → 마크다운 표는 한 줄이어야 하므로 공백 1칸으로 바꾼다
CELL_SPACES = re.compile(r"\s+")

# 큰 표를 나누는 기준: 머리글을 뺀 본문 행 수 (명세서 split_big_table 행: max_rows 기본값 10)
MAX_TABLE_ROWS = 10


def _clean_cell(cell: str | None) -> str:
	"""셀 1칸을 마크다운 표에 넣을 글로 바꾼다.

	- None → 빈칸 "" (pdfplumber는 병합된 칸·빈 칸을 None으로 돌려준다)
	- 줄바꿈·연속 공백 → 공백 1칸, 앞뒤 공백 제거
	- '|'는 마크다운 표의 칸 나눔 기호라, 글자로 남기도록 '\\|'로 바꾼다 (칸 수가 늘어나지 않게)
	- str()로 감싸는 이유: 비교표·대시보드에서 숫자를 그대로 넘겨도 표가 만들어지게
	"""
	if cell is None:
		return ""
	text = CELL_SPACES.sub(" ", str(cell)).strip()
	return text.replace("|", "\\|")


def _markdown_row(cells: list[str]) -> str:
	"""칸 목록을 마크다운 표 한 줄로 만든다. 예) ['연도', '물가'] → '| 연도 | 물가 |'"""
	return "| " + " | ".join(cells) + " |"


def table_to_markdown(rows: list[list[str | None]]) -> str:
	"""rows(첫 행 = 머리글)를 LLM이 읽기 쉬운 마크다운 표 글 1개로 만든다.

	순서 (명세서 table_to_markdown 행): 머리글 줄 → 구분선 줄(|---|---|) → 본문 줄
		| 연도 | 물가상승률 |
		|---|---|
		| 2025 | 2.1 |
	- 셀 정리는 _clean_cell() (None은 빈칸, 줄바꿈은 공백, '|'는 '\\|')
	- rows가 비어 있으면 "" (만들 표가 없음)
	"""
	if not rows:
		return ""
	header, *body = [[_clean_cell(cell) for cell in row] for row in rows]
	lines = [_markdown_row(header), "|" + "|".join(["---"] * len(header)) + "|"]
	lines += [_markdown_row(row) for row in body]
	return "\n".join(lines)


def split_big_table(
	rows: list[list[str]],
	max_rows: int = MAX_TABLE_ROWS,
	header_rows: int = 1,
) -> list[list[list[str]]]:
	"""본문이 max_rows행을 넘는 표를 max_rows행씩 나누고, 모든 조각 맨 앞에 머리글을 붙인다.

	명세서 split_big_table 행 그대로 (header_rows=1, 기본값)
	- 머리글 = 첫 줄 rows[0]. 표가 잘려도 조각마다 머리글이 있어서, 조각 하나만 검색·답변에 쓰여도 각 칸이 무엇인지 알 수 있다
	  예) 본문 23행 → [머리글 + 1~10행], [머리글 + 11~20행], [머리글 + 21~23행]
	- 본문이 max_rows행 이하면 나누지 않고 [rows] 그대로
	- rows가 비어 있으면 [] (만들 조각이 없음)
	- 조각 캡션의 '(1/n)'은 표 노드(extract_tables_node)가 붙인다
	- max_rows가 1보다 작으면 ValueError (0이면 나눌 수 없고, 음수면 조각이 0개가 되어 표가 사라짐)

	[추가 기능 · 명세서 밖] header_rows: 머리글 줄 수 (기본 1 = 명세서와 같음)
	- 왜: HRI 전망 표는 머리글이 2줄인 경우가 있다 (1줄 '2025 | 2026 | 2027(E)', 2줄 '상반 | 하반 | 연간').
	  첫 줄만 붙이면 둘째 조각부터 '상반·하반·연간' 줄이 빠져 숫자가 어느 칸인지 알 수 없다
	  (HRI 5편 점검: 본문 10행 초과 표 9개 중 5개가 이 모양)
	- 어떻게: 앞 header_rows줄을 머리글로 보고 조각마다 모두 붙인다. 본문 행 수도 그 줄을 뺀 나머지로 센다
	  예) header_rows=2, 본문 12행 → [머리글 2줄 + 1~10행], [머리글 2줄 + 11~12행]
	- 몇 줄인지는 이 함수가 판단하지 않는다. 부르는 쪽이 정해서 넘긴다
	  (지금 표 노드는 넘기지 않아 1줄로 동작. 표마다 줄 수를 정하는 규칙은 3.2.10 extract_tables에서 정할 일)
	- header_rows가 1보다 작으면 ValueError (머리글 없이 나누면 조각의 칸 이름이 사라짐)
	"""
	if max_rows < 1:
		raise ValueError(f"max_rows는 1 이상이어야 합니다: {max_rows}")
	if header_rows < 1:
		raise ValueError(f"header_rows는 1 이상이어야 합니다: {header_rows}")
	if not rows:
		return []
	header, body = rows[:header_rows], rows[header_rows:]
	if len(body) <= max_rows:
		return [rows]
	return [header + body[start:start + max_rows] for start in range(0, len(body), max_rows)]


# ── summarize_tables (WBS 3.2.11) ─────────────────────────────────────────────
# 요약 LLM 호출을 동시에 몇 개까지 보낼지 (process_charts_node의 CHART_MAX_CONCURRENCY와 같은 값, 10/28 결정)
SUMMARY_MAX_CONCURRENCY = 4

# 요약 규칙 (명세서 summarize_tables 행: 표·차트마다 1~2문장, 차트는 제목·단위·추세·최댓값/최솟값 포함)
# 수치·단위·'원문에 없는 내용 금지' 줄은 summary_prompt(김희진)의 섹션 요약 규칙과 같게 썼다.
TABLE_SUMMARY_SYSTEM_PROMPT = (
	"당신은 경제 보고서를 요약하는 애널리스트입니다.\n"
	"주어진 표 1개가 무엇을 보여 주는지 1~2문장으로 요약하세요. 이 요약은 표를 검색하는 데 쓰입니다.\n"
	"- 수치와 단위는 원문 그대로 씁니다.\n"
	"- 원문에 없는 내용은 쓰지 않습니다.\n"
	"- 요약 문장만 쓰고, 앞에 번호나 기호를 붙이지 않습니다."
)

# 차트(source_type='chart')일 때만 덧붙이는 줄 (명세서: 차트는 제목·단위·추세·최댓값/최솟값 포함)
CHART_SUMMARY_RULE = "- 이 표는 차트에서 읽어 낸 값입니다. 차트 제목, 단위, 추세(오르내림), 최댓값과 최솟값을 반드시 넣습니다."

# 요약에 실패했을 때 남기는 글 (10/28 결정: 그 표만 summary=None, 로그를 남기고 업로드 화면에도 경고)
SUMMARY_FAILED_MESSAGE = "요약에 실패한 표입니다"


def _summary_messages(table: Table) -> list[tuple[str, str]]:
	"""표 1개를 요약 LLM에 보낼 메시지로 만든다. 캡션(표 제목)과 마크다운 원본을 그대로 넘긴다."""
	system = TABLE_SUMMARY_SYSTEM_PROMPT
	if table.source_type == "chart":
		system += "\n" + CHART_SUMMARY_RULE
	return [
		("system", system),
		("human", f"표 제목: {table.caption or '(제목 없음)'}\n\n표:\n{table.markdown}"),
	]


def summarize_tables(tables: list[Table]) -> list[Table]:
	"""표·차트마다 검색용 1~2문장 요약을 만들어 Table.summary를 채운 새 목록을 돌려준다 (입력 순서 그대로).

	명세서 summarize_tables 행 (검색용/답변용 분리)
	- get_chat_model(temperature=0)으로 표·차트마다 1~2문장 요약. 차트는 제목·단위·추세·최댓값/최솟값 포함
	- 요약은 표·차트 청크의 text(캡션+요약)가 되어 검색에 쓰이고,
	  답변 때는 doc_id로 report_tables의 원본 markdown을 쓴다 → markdown·doc_id 등 다른 값은 바꾸지 않는다
	  (model_copy로 summary만 바꿈. doc_id가 그대로라 노드가 같은 표의 청크를 찾을 수 있다)
	- 표가 0개면 LLM을 부르지 않고 []

	호출 방식 (10/28 결정): 모델.batch(..., max_concurrency=4, return_exceptions=True)
	- 표마다 메시지를 만들어 한 번에 넘기고, 동시에 최대 4개씩 보낸다 (process_charts_node와 같은 방식)
	- return_exceptions=True: 표 하나가 실패해도 나머지는 계속 요약한다

	실패 처리 (10/28 결정): API 오류·빈 응답이면 그 표만 summary=None으로 두고 로그에 '요약에 실패한 표입니다'를 남긴다
	- summarize_tables_node가 summary가 None인 표를 warnings(업로드 화면)에 알리고, 청크 글은 '캡션+원본 마크다운'으로 채운다

	get_chat_model은 함수 안에서 import한다: hri.llm.llm_client는 import할 때 .env를 읽으므로
	맨 위에서 import하면 표 변환 함수(table_to_markdown 등)만 쓰는 곳·테스트도 .env가 있어야 한다.
	"""
	if not tables:
		return []

	from hri.llm.llm_client import get_chat_model

	model = get_chat_model(temperature=0)
	responses = model.batch(
		[_summary_messages(table) for table in tables],
		config={"max_concurrency": SUMMARY_MAX_CONCURRENCY},
		return_exceptions=True,
	)

	summarized: list[Table] = []
	for table, response in zip(tables, responses):
		if isinstance(response, Exception):
			logger.error("%s쪽 표 %s: %s", table.page, table.caption, SUMMARY_FAILED_MESSAGE, exc_info=response)
			summary = None
		else:
			summary = response.content.strip() or None
			if summary is None:
				logger.error("%s쪽 표 %s: %s (빈 응답)", table.page, table.caption, SUMMARY_FAILED_MESSAGE)
		summarized.append(table.model_copy(update={"summary": summary}))
	return summarized

# ── extract_tables · validate_table (WBS 3.2.10) ──────────────────────────────
# 표 캡션: '[표 n] 제목'(그 줄 끝까지) / HRI '< 제목 >'(꺾쇠 안 전체, 두 줄이어도 됨). 빈 꺾쇠 '< >'는 캡션이 아님
# '< 그림 n >'·'< 차트 n >'은 차트 캡션이라 뺀다 (chart_loader.CAPTION_PATTERN과 같은 기준으로 서로 나눠 가진다)
CAPTION = re.compile(r"\[\s*표\s*[\d\-.]+\s*\][^\[\]<>\n]*|<\s*(?!(?:그림|차트))[^<>\s][^<>]*>")

# 캡션을 찾는 범위: 표 위·아래 40pt 안 (chart_loader.CAPTION_GAP과 같은 값)
CAPTION_GAP = 40

# 같은 줄로 볼 낱말의 위쪽 좌표 차이 (pt). 글자 크기가 달라 1~2pt씩 어긋나는 낱말을 한 줄로 묶는다
LINE_TOLERANCE = 3

# 표로 인정하는 최소 크기. 1행이나 1열짜리는 테두리 상자·글상자이지 표가 아니다
MIN_TABLE_ROWS = 2
MIN_TABLE_COLS = 2

# validate_table: 이상이 없을 때 돌려주는 글 (표 노드는 '✅'로 시작하는지로 경고 여부를 가른다)
TABLE_OK_MESSAGE = "✅ 이상 없음"

# validate_table: 경고에 좌표를 적는 최대 개수. 넘으면 '외 n개'로 줄인다 (경고 한 줄이 너무 길어지지 않게)
MAX_LISTED_CELLS = 10


def _is_blank(cell: str | None) -> bool:
	"""칸이 비어 있는지. None(병합 자리)도 비어 있는 것으로 본다."""
	return cell is None or not cell.strip()


def _clean_rows(raw_rows: list[list[str | None]]) -> list[list[str | None]]:
	"""pdfplumber가 돌려준 rows를 다듬는다.

	- 글자 칸은 앞뒤 공백만 지운다 (칸 안의 줄바꿈은 table_to_markdown이 처리하므로 그대로 둔다)
	- 칸이 비어 있으면 "" 그대로, 병합된 칸 자리는 None 그대로 둔다 (validate_table이 둘을 구분해서 센다)
	- 모든 칸이 비어 있는 행(""·None뿐)은 버린다 (표 사이 빈 줄·테두리 흔적)
	"""
	rows = [[cell.strip() if isinstance(cell, str) else None for cell in row] for row in raw_rows]
	return [row for row in rows if not all(_is_blank(cell) for cell in row)]


def _overlaps_horizontally(word: dict, bbox: tuple) -> bool:
	"""낱말이 bbox의 가로 범위와 겹치는지 (나란히 놓인 두 표의 캡션을 섞지 않게)"""
	return word["x1"] > bbox[0] and word["x0"] < bbox[2]


def _near_text(words: list[dict], bbox: tuple, others: list[tuple], above: bool) -> str:
	"""bbox 바로 위(above=True) 또는 아래 CAPTION_GAP 안의 낱말을 읽는 순서로 이은 글. 줄은 '\\n'으로 잇는다.

	다른 표의 위쪽 캡션 자리에 있는 낱말은 뺀다: 표 A 아래에 있는 글이 사실은 바로 아래 표 B의 캡션일 수 있다.
	"""
	top, bottom = bbox[1], bbox[3]
	picked = []
	for word in words:
		if not _overlaps_horizontally(word, bbox):
			continue
		if above and top - CAPTION_GAP <= word["bottom"] <= top + 2:
			picked.append(word)
		elif not above and bottom - 2 <= word["top"] <= bottom + CAPTION_GAP:
			picked.append(word)
	if not above:
		picked = [
			word for word in picked
			if not any(
				_overlaps_horizontally(word, other) and other[1] > bottom and other[1] - CAPTION_GAP <= word["bottom"] <= other[1] + 2
				for other in others
			)
		]

	# 위 → 아래, 왼 → 오른쪽 순서. 위쪽 좌표 차이가 LINE_TOLERANCE 이하면 같은 줄
	picked.sort(key=lambda word: (word["top"], word["x0"]))
	lines: list[list[dict]] = []
	for word in picked:
		if lines and abs(word["top"] - lines[-1][0]["top"]) <= LINE_TOLERANCE:
			lines[-1].append(word)
		else:
			lines.append([word])
	return "\n".join(" ".join(word["text"] for word in sorted(line, key=lambda w: w["x0"])) for line in lines)


def _find_caption(words: list[dict], bbox: tuple, others: list[tuple]) -> str | None:
	"""표 위에서 먼저, 없으면 아래에서 캡션을 찾는다. 줄바꿈·연속 공백은 공백 1칸으로 정리한다. 없으면 None"""
	for above in (True, False):
		match = CAPTION.search(_near_text(words, bbox, others, above))
		if match:
			return " ".join(match.group().split())
	return None


def extract_tables(file_path: str) -> list[Table]:
	"""
	PDF에서 표를 행·열 구조 그대로 뽑아 Table(source_type='table') 목록으로 돌려준다.

	1) pdfplumber page.find_tables()로 쪽마다 표를 찾고, 표마다 extract()로 rows를 만든다.
	   page는 page.page_number (1부터). 쪽 순서, 쪽 안에서는 위 → 아래, 왼 → 오른쪽 순서로 돌려준다.
	2) rows는 첫 행이 머리글이다. 칸이 비어 있으면 "", 병합된 칸 자리는 None으로 두고 (pdfplumber 그대로),
	   앞뒤 공백만 지우며, 모든 칸이 빈 행은 버린다 (_clean_rows).
	3) 1행이나 1열짜리, 글자가 하나도 없는 것은 표가 아니라 테두리 상자라서 뺀다 (MIN_TABLE_ROWS·MIN_TABLE_COLS).
	4) 표 위·아래 CAPTION_GAP(40pt) 안에서 캡션('[표 n] 제목', HRI '< 제목 >')을 찾는다. 위를 먼저 보고, 없으면 아래. 없으면 None.
	   '< 그림 n >'·'< 차트 n >'은 차트 캡션이라 표 캡션으로 쓰지 않는다.

	markdown은 비워 둔다: 표 노드(extract_tables_node)가 조각마다 table_to_markdown()으로 채운다.
	표가 없으면 빈 목록. PDF를 열지 못하면 pdfplumber의 예외를 그대로 낸다 (업로드 때 validate_pdf가 먼저 걸러 준다).
	pdfplumber는 함수 안에서 import한다: 표 변환 함수(table_to_markdown 등)만 쓰는 곳은 PDF 라이브러리를 불러오지 않게.
	"""
	import pdfplumber

	tables: list[Table] = []
	with pdfplumber.open(file_path) as pdf:
		for page in pdf.pages:
			found = []
			for table in sorted(page.find_tables(), key=lambda t: (t.bbox[1], t.bbox[0])):
				rows = _clean_rows(table.extract())
				if len(rows) < MIN_TABLE_ROWS or max((len(row) for row in rows), default=0) < MIN_TABLE_COLS:
					continue
				found.append((table.bbox, rows))
			if not found:
				continue

			words = page.extract_words()
			boxes = [bbox for bbox, _rows in found]
			for bbox, rows in found:
				others = [other for other in boxes if other is not bbox]
				tables.append(Table(page=page.page_number, caption=_find_caption(words, bbox, others), rows=rows))
	return tables


def _format_cells(cells: list[tuple[int, int]]) -> str:
	"""좌표 목록을 '[행, 열: (2, 3), (4, 1) 외 5개]'로 만든다. MAX_LISTED_CELLS개까지만 적는다."""
	listed = ", ".join(f"({row}, {col})" for row, col in cells[:MAX_LISTED_CELLS])
	rest = len(cells) - MAX_LISTED_CELLS
	return f"[행, 열: {listed}{f' 외 {rest}개' if rest > 0 else ''}]"


def validate_table(rows: list[list[str | None]]) -> str:
	"""추출한 표의 빈 칸·병합 의심 칸을 찾아 '✅ 이상 없음' 또는 경고 글을 돌려준다.

	- 빈 칸: 글자가 없는 칸 ("" 또는 공백뿐)
	- 병합 의심 칸: None인 칸. pdfplumber는 병합된 칸의 둘째 이후 자리를 None으로 돌려준다
	- 좌표는 (행, 열)이고 1부터 센다 (1행 = 머리글). 각각 MAX_LISTED_CELLS개까지만 적고 넘으면 '외 n개'
	- 이상이 없으면 TABLE_OK_MESSAGE. 표 노드는 이 글이 '✅'로 시작하지 않으면 경고로 보고 warnings에 남긴다 (표는 그대로 저장)
	- 행이 하나도 없으면 경고

	예) '⚠️ 빈 칸 2개 [행, 열: (3, 2), (5, 1)] / 병합 의심 칸 1개 [행, 열: (1, 3)]'
	"""
	if not rows:
		return "⚠️ 표에 행이 없습니다"

	empty: list[tuple[int, int]] = []
	merged: list[tuple[int, int]] = []
	for row_no, row in enumerate(rows, start=1):
		for col_no, cell in enumerate(row, start=1):
			if cell is None:
				merged.append((row_no, col_no))
			elif not cell.strip():
				empty.append((row_no, col_no))

	parts = []
	if empty:
		parts.append(f"빈 칸 {len(empty)}개 {_format_cells(empty)}")
	if merged:
		parts.append(f"병합 의심 칸 {len(merged)}개 {_format_cells(merged)}")
	return "⚠️ " + " / ".join(parts) if parts else TABLE_OK_MESSAGE