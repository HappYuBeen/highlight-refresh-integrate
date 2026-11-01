"""표 추출·검수 테스트 · extract_tables·validate_table (WBS 3.2.10)

네트워크·DB·LLM·.env 없이 실행한다.
- 표가 있는 PDF를 pymupdf로 직접 그려 만든다 (선으로 칸을 그리고 한글 글꼴 'korea'로 글자를 넣음).
  pdfplumber는 칸 선을 보고 표를 찾으므로, 그린 표가 그대로 표로 잡힌다.
- extract_tables_node·validate_table은 가짜 없이 진짜 함수끼리 이어서 확인한다 (노드 연결 확인).

실행: poetry run python -m pytest tests/test_table_extract.py -v
"""

import pymupdf
import pytest

from hri.domain.schemas import Table
from hri.graph.ingest_graph import TABLE_WARNING, extract_tables_node
from hri.loader.table_loader import (
	CAPTION,
	CAPTION_GAP,
	MAX_LISTED_CELLS,
	TABLE_OK_MESSAGE,
	_clean_rows,
	extract_tables,
	table_to_markdown,
	validate_table,
)

CELL_W, CELL_H = 100, 24


def draw_text(page, x, y, text, size=10):
	"""(x, y) 왼쪽 아래 기준으로 한글 글꼴 글자를 넣는다"""
	page.insert_text((x, y), text, fontname="korea", fontsize=size)


def draw_table(page, x0, y0, rows):
	"""rows(칸 글자 목록)대로 선으로 칸을 그린다. None이나 ''인 칸은 글자를 넣지 않는다. 표의 아래 y를 돌려준다."""
	for i, row in enumerate(rows):
		for j, text in enumerate(row):
			rect = pymupdf.Rect(x0 + j * CELL_W, y0 + i * CELL_H, x0 + (j + 1) * CELL_W, y0 + (i + 1) * CELL_H)
			page.draw_rect(rect)
			if text:
				draw_text(page, rect.x0 + 5, rect.y1 - 8, text)
	return y0 + len(rows) * CELL_H


def make_pdf(tmp_path, pages, name="t.pdf"):
	"""pages: 쪽마다 page를 받아 그리는 함수 목록. 만든 PDF 경로를 돌려준다."""
	doc = pymupdf.open()
	for draw in pages:
		draw(doc.new_page(width=595, height=842))
	path = tmp_path / name
	doc.save(str(path))
	doc.close()
	return str(path)


GROWTH = [["구분", "2025", "2026"], ["성장률", "2.1", "1.8"], ["물가", "2.3", "2.0"]]


# ─────────────────────────────────────────────────────────────
# 1. extract_tables
# ─────────────────────────────────────────────────────────────

# [확인 1] 표 1개: 행·열 그대로(rows), 쪽 번호 1부터, 표 위의 '[표 n]' 캡션, source_type='table', markdown은 비워 둠(표 노드가 채움)
def test_extract_tables_basic(tmp_path):
	def page(p):
		draw_text(p, 72, 60, "[표 1] 주요 지표 전망")
		draw_table(p, 72, 80, GROWTH)

	tables = extract_tables(make_pdf(tmp_path, [page]))

	assert len(tables) == 1
	table = tables[0]
	assert isinstance(table, Table)
	assert (table.page, table.caption, table.source_type) == (1, "[표 1] 주요 지표 전망", "table")
	assert table.rows == GROWTH
	assert table.markdown == "" and table.summary is None


# [확인 2] 표가 없는 PDF·글자만 있는 쪽은 빈 목록
def test_extract_tables_no_table(tmp_path):
	def page(p):
		draw_text(p, 72, 72, "표가 없는 쪽입니다.")

	assert extract_tables(make_pdf(tmp_path, [page])) == []


# [확인 3] 여러 쪽: 쪽 순서, 쪽 안에서는 위 → 아래. 표 없는 쪽은 건너뛰되 쪽 번호는 실제 쪽 번호
def test_extract_tables_pages_and_order(tmp_path):
	def page1(p):
		draw_table(p, 72, 400, [["아래", "표"], ["1", "2"]])
		draw_table(p, 72, 100, [["위", "표"], ["3", "4"]])

	def page2(p):
		draw_text(p, 72, 72, "글자만")

	def page3(p):
		draw_table(p, 72, 100, [["셋째", "쪽"], ["5", "6"]])

	tables = extract_tables(make_pdf(tmp_path, [page1, page2, page3]))

	assert [(t.page, t.rows[0][0]) for t in tables] == [(1, "위"), (1, "아래"), (3, "셋째")]


# [확인 4] 빈 칸은 "", 병합된 칸 자리는 None으로 pdfplumber 그대로 둔다
def test_extract_tables_empty_and_merged(tmp_path):
	def page(p):
		draw_table(p, 72, 80, [["구분", "2025", "2026"], ["물가", "", "2.0"]])
		# 머리글 '연간'이 둘째·셋째 칸에 걸친 병합: 칸 하나 짜리 상자 + 두 칸 폭 상자
		p.draw_rect(pymupdf.Rect(72, 250, 172, 274))
		p.draw_rect(pymupdf.Rect(172, 250, 372, 274))
		draw_text(p, 177, 266, "연간")
		draw_table(p, 72, 274, [["구분", "상반", "하반"]])

	first, second = extract_tables(make_pdf(tmp_path, [page]))

	assert first.rows[1] == ["물가", "", "2.0"]
	assert second.rows == [["", "연간", None], ["구분", "상반", "하반"]]


# [확인 5] 칸 글자 앞뒤 공백을 지우고, 칸 안에서 줄이 바뀐 글은 줄바꿈 그대로 둔다 (table_to_markdown이 공백으로 바꿈)
def test_extract_tables_cell_text(tmp_path):
	def page(p):
		p.draw_rect(pymupdf.Rect(72, 80, 272, 120))
		p.draw_rect(pymupdf.Rect(272, 80, 372, 120))
		draw_text(p, 77, 95, "첫째 줄")
		draw_text(p, 77, 110, "둘째 줄")
		draw_text(p, 277, 105, "값")
		p.draw_rect(pymupdf.Rect(72, 120, 272, 144))
		p.draw_rect(pymupdf.Rect(272, 120, 372, 144))
		draw_text(p, 77, 136, "x")
		draw_text(p, 277, 136, "y")

	table = extract_tables(make_pdf(tmp_path, [page]))[0]

	assert table.rows[0] == ["첫째 줄\n둘째 줄", "값"]
	assert "| 첫째 줄 둘째 줄 | 값 |" in table_to_markdown(table.rows)


# [확인 6] 1행이나 1열짜리 상자, 글자가 하나도 없는 표는 표로 보지 않는다
def test_extract_tables_skips_boxes(tmp_path):
	def page(p):
		p.draw_rect(pymupdf.Rect(72, 80, 372, 140))
		draw_text(p, 80, 100, "글상자 안의 글")
		draw_table(p, 72, 200, [["한", "줄"]])
		draw_table(p, 72, 300, [["하나"], ["열"]])
		draw_table(p, 72, 400, [["", ""], ["", ""]])

	assert extract_tables(make_pdf(tmp_path, [page])) == []


# [확인 6-1] 칸 글자 앞뒤 공백은 지우고, ""와 None(병합 자리)은 그대로 구분해 둔다
def test_clean_rows_strips_but_keeps_empty_and_none():
	assert _clean_rows([["  구분 ", None, ""], [" 값\n둘째 줄 ", "2.0 ", "x"]]) == [["구분", None, ""], ["값\n둘째 줄", "2.0", "x"]]


# [확인 7] 모든 칸이 빈 행은 버린다
def test_extract_tables_drops_blank_rows(tmp_path):
	def page(p):
		draw_table(p, 72, 80, [["구분", "값"], ["", ""], ["물가", "2.0"]])

	assert extract_tables(make_pdf(tmp_path, [page]))[0].rows == [["구분", "값"], ["물가", "2.0"]]


# ─────────────────────────────────────────────────────────────
# 2. 캡션
# ─────────────────────────────────────────────────────────────

# [확인 8] HRI '< 제목 >' 캡션, 두 줄로 나뉜 캡션은 한 줄로 이음
@pytest.mark.parametrize("lines, expected", [
	(["< 주요국 경제성장률 전망 >"], "< 주요국 경제성장률 전망 >"),
	(["< 한국의 민간신용(GDP대비)", "비율 추이 >"], "< 한국의 민간신용(GDP대비) 비율 추이 >"),
	(["[표 2] 부문별 전망", "자료 : 한국은행"], "[표 2] 부문별 전망"),
])
def test_caption_above(tmp_path, lines, expected):
	def page(p):
		for i, line in enumerate(lines):
			draw_text(p, 72, 50 + i * 12, line)
		draw_table(p, 72, 50 + len(lines) * 12 + 6, GROWTH)

	assert extract_tables(make_pdf(tmp_path, [page]))[0].caption == expected


# [확인 8-1] 같은 줄 낱말의 높이가 1~2pt 어긋나도(글꼴이 달라 생김) 왼쪽부터 읽는 순서로 잇는다
def test_caption_words_with_uneven_height(tmp_path):
	def page(p):
		draw_text(p, 72, 60, "[표 1]")
		draw_text(p, 160, 58, "물가")  # 2pt 위
		draw_text(p, 200, 60, "전망")
		draw_table(p, 72, 80, GROWTH)

	assert extract_tables(make_pdf(tmp_path, [page]))[0].caption == "[표 1] 물가 전망"


# [확인 9] 위에 캡션이 없으면 아래에서 찾고, 범위(CAPTION_GAP) 밖이면 None
def test_caption_below_and_out_of_range(tmp_path):
	def near(p):
		bottom = draw_table(p, 72, 80, GROWTH)
		draw_text(p, 72, bottom + 14, "[표 3] 표 아래 캡션")

	def far(p):
		bottom = draw_table(p, 72, 80, GROWTH)
		draw_text(p, 72, bottom + CAPTION_GAP + 30, "[표 4] 너무 먼 캡션")

	assert extract_tables(make_pdf(tmp_path, [near], "near.pdf"))[0].caption == "[표 3] 표 아래 캡션"
	assert extract_tables(make_pdf(tmp_path, [far], "far.pdf"))[0].caption is None


# [확인 9-1] 표 위·아래에 모두 캡션이 있으면 위의 것을 쓴다
def test_caption_prefers_above(tmp_path):
	def page(p):
		draw_text(p, 72, 60, "[표 1] 위 캡션")
		bottom = draw_table(p, 72, 80, GROWTH)
		draw_text(p, 72, bottom + 14, "[표 2] 아래 캡션")

	assert extract_tables(make_pdf(tmp_path, [page]))[0].caption == "[표 1] 위 캡션"


# [확인 10] 캡션이 없거나, 표 옆에 나란히 놓인 다른 글의 캡션은 가져오지 않는다
def test_caption_none_and_side_by_side(tmp_path):
	def page(p):
		draw_text(p, 400, 70, "[표 9] 오른쪽 글")  # 표 가로 범위(72~372) 밖
		draw_table(p, 72, 80, GROWTH)

	assert extract_tables(make_pdf(tmp_path, [page]))[0].caption is None


# [확인 11] 차트 캡션('< 그림 n >'·'< 차트 n >')은 표 캡션이 아니다
@pytest.mark.parametrize("text", ["< 그림 1 >", "<차트 2>", "[그림 3] 차트 제목"])
def test_chart_caption_is_not_table_caption(tmp_path, text):
	def page(p):
		draw_text(p, 72, 60, text)
		draw_table(p, 72, 80, GROWTH)

	assert extract_tables(make_pdf(tmp_path, [page]))[0].caption is None


# [확인 12] 위아래로 붙은 두 표: 아래 표의 캡션을 위 표가 가져가지 않는다
def test_caption_not_stolen_by_table_above(tmp_path):
	def page(p):
		first_bottom = draw_table(p, 72, 80, GROWTH)
		draw_text(p, 72, first_bottom + 18, "[표 2] 아래 표 제목")
		draw_table(p, 72, first_bottom + 24, GROWTH)

	first, second = extract_tables(make_pdf(tmp_path, [page]))

	assert first.caption is None
	assert second.caption == "[표 2] 아래 표 제목"


# [확인 13] 캡션 정규식: 빈 꺾쇠 '< >'는 캡션이 아니다
@pytest.mark.parametrize("text, found", [
	("< >", None), ("<>", None), ("[표 1]", "[표 1]"), ("[표 1-2] 제목", "[표 1-2] 제목"),
	("< 제목 >", "< 제목 >"), ("본문 < 제목 > 뒤", "< 제목 >"), ("< 그림 1 >", None),
])
def test_caption_pattern(text, found):
	match = CAPTION.search(text)
	assert (match.group() if match else None) == found


# ─────────────────────────────────────────────────────────────
# 3. validate_table
# ─────────────────────────────────────────────────────────────

# [확인 14] 이상 없으면 '✅ 이상 없음'
def test_validate_table_ok():
	assert validate_table(GROWTH) == TABLE_OK_MESSAGE == "✅ 이상 없음"


# [확인 15] 빈 칸·병합 의심 칸(None)의 (행, 열) 좌표, 1부터, 1행이 머리글
def test_validate_table_cells():
	assert validate_table([["구분", "", "2026"], ["물가", "2.0", ""]]) == "⚠️ 빈 칸 2개 [행, 열: (1, 2), (2, 3)]"
	assert validate_table([["연간", None, None], ["상반", "하반", "합계"]]) == "⚠️ 병합 의심 칸 2개 [행, 열: (1, 2), (1, 3)]"
	assert validate_table([["구분", "", None], ["물가", "2.0", "1.0"]]) == (
		"⚠️ 빈 칸 1개 [행, 열: (1, 2)] / 병합 의심 칸 1개 [행, 열: (1, 3)]"
	)


# [확인 16] 공백뿐인 칸도 빈 칸
def test_validate_table_whitespace_cell():
	assert validate_table([["구분", "  "], ["물가", "2.0"]]) == "⚠️ 빈 칸 1개 [행, 열: (1, 2)]"


# [확인 17] 좌표가 MAX_LISTED_CELLS개를 넘으면 '외 n개'로 줄인다
def test_validate_table_listing_limit():
	rows = [["구분"] + [""] * 14]  # 빈 칸 14개
	result = validate_table(rows)

	assert result.startswith("⚠️ 빈 칸 14개 [행, 열: (1, 2),")
	assert MAX_LISTED_CELLS == 10
	assert result.count("(1,") == 10
	assert result.endswith("외 4개]")


# [확인 18] 행이 없으면 경고
def test_validate_table_no_rows():
	assert validate_table([]).startswith("⚠️")


# ─────────────────────────────────────────────────────────────
# 4. 노드 연결 (가짜 없이 진짜 함수끼리)
# ─────────────────────────────────────────────────────────────

# [확인 19] extract_tables_node: 진짜 extract_tables·validate_table·split_big_table·table_to_markdown로 표를 만들고,
#           빈 칸이 있는 표는 경고, 12행 표는 두 조각((1/2)·(2/2))으로 나눈다
def test_extract_tables_node_with_real_functions(tmp_path):
	big = [["연도", "값"]] + [[str(2000 + i), f"{i}.0"] for i in range(12)]

	def page(p):
		draw_text(p, 72, 60, "[표 1] 빈 칸이 있는 표")
		bottom = draw_table(p, 72, 80, [["구분", "2025"], ["물가", ""]])
		draw_text(p, 72, bottom + 40, "[표 2] 큰 표")
		draw_table(p, 72, bottom + 50, big)

	result = extract_tables_node({"file_path": make_pdf(tmp_path, [page])})

	assert [(t.page, t.caption) for t in result["tables"]] == [
		(1, "[표 1] 빈 칸이 있는 표"), (1, "[표 2] 큰 표 (1/2)"), (1, "[표 2] 큰 표 (2/2)"),
	]
	assert result["tables"][0].markdown == "| 구분 | 2025 |\n|---|---|\n| 물가 |  |"
	assert [len(t.rows) for t in result["tables"][1:]] == [11, 3]
	assert result["warnings"] == [TABLE_WARNING.format(page=1, caption="[표 1] 빈 칸이 있는 표", result="⚠️ 빈 칸 1개 [행, 열: (2, 2)]")]