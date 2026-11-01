"""get_report_text 로직 테스트 · 가짜 섹션·표 조회 사용 (장유빈)"""
from hri.domain.schemas import Section, Table
from hri.service import report_service
from hri.service.report_service import get_report_text


def _section(no, start, end, title="제목", text="원문") -> Section:
	return Section(report_id=1, section_no=no, title=title, start_page=start, end_page=end, text=text)


def _table(page, markdown="| a |\n|---|\n| 1 |", caption=None, source_type="table", needs_review=False) -> Table:
	return Table(
		doc_id=f"doc-{page}-{caption}-{markdown}", report_id=1, page=page, caption=caption,
		markdown=markdown, source_type=source_type, needs_review=needs_review,
	)


def _use(monkeypatch, sections, tables):
	"""조회 함수를 가짜로 바꾸고, 어떤 report_id로 불렸는지 기록한다."""
	called = {}

	def fake_sections(report_id):
		called["sections"] = report_id
		return sections

	def fake_tables(report_id):
		called["tables"] = report_id
		return tables

	monkeypatch.setattr(report_service, "select_sections", fake_sections)
	monkeypatch.setattr(report_service, "select_report_tables", fake_tables)
	return called


def test_sections_joined_in_page_order(monkeypatch):
	# DB에서 순서가 뒤섞여 와도 시작 쪽 순으로 잇는다
	_use(monkeypatch, [_section(1, 5, 8, "두 번째", "B"), _section(0, 1, 4, "첫 번째", "A")], [])
	assert get_report_text(7) == "## 첫 번째 (p.1-4)\nA\n\n## 두 번째 (p.5-8)\nB"


def test_passes_report_id_to_both_queries(monkeypatch):
	called = _use(monkeypatch, [], [])
	get_report_text(7)
	assert called == {"sections": 7, "tables": 7}


def test_table_goes_under_section_containing_its_page(monkeypatch):
	_use(
		monkeypatch,
		[_section(0, 1, 4, "A", "원문A"), _section(1, 5, 8, "B", "원문B")],
		[_table(3, "TABLE3", caption="[표 1]"), _table(6, "TABLE6")],
	)
	assert get_report_text(1) == (
		"## A (p.1-4)\n원문A\n\n"
		"[표 p.3 / [표 1]]\nTABLE3\n\n"
		"## B (p.5-8)\n원문B\n\n"
		"[표 p.6]\nTABLE6"
	)


def test_table_before_first_section_goes_first(monkeypatch):
	_use(monkeypatch, [_section(0, 3, 5, "A", "원문A")], [_table(1, "TABLE1")])
	assert get_report_text(1) == "[표 p.1]\nTABLE1\n\n## A (p.3-5)\n원문A"


def test_chart_label_and_needs_review_mark(monkeypatch):
	chart = _table(2, "CHART", caption="그림 3", source_type="chart", needs_review=True)
	_use(monkeypatch, [_section(0, 1, 4)], [chart])
	assert "[차트 p.2 / 그림 3] (추정)\nCHART" in get_report_text(1)


def test_confirmed_chart_has_no_estimate_mark(monkeypatch):
	chart = _table(2, "CHART", caption="그림 3", source_type="chart", needs_review=False)
	_use(monkeypatch, [_section(0, 1, 4)], [chart])
	text = get_report_text(1)
	assert "[차트 p.2 / 그림 3]\nCHART" in text
	assert "(추정)" not in text


def test_two_tables_on_same_page_keep_given_order(monkeypatch):
	_use(monkeypatch, [_section(0, 1, 4)], [_table(2, "FIRST"), _table(2, "SECOND", caption="x")])
	text = get_report_text(1)
	assert text.index("FIRST") < text.index("SECOND")


def test_tables_only(monkeypatch):
	_use(monkeypatch, [], [_table(2, "ONLY")])
	assert get_report_text(1) == "[표 p.2]\nONLY"


def test_empty_report_returns_empty_string(monkeypatch):
	_use(monkeypatch, [], [])
	assert get_report_text(1) == ""