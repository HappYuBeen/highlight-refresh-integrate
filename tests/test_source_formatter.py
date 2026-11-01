"""format_sources 로직 테스트 (장유빈)"""
from hri.domain.schemas.qa import ContextDoc
from hri.source.source_formatter import format_sources


def _doc(**kwargs) -> ContextDoc:
	base = dict(
		report_id=1, report_title="2026 경제전망", kind="text", section_title="물가",
		caption=None, page_start=12, page_end=13, text="본문", needs_review=False,
	)
	base.update(kwargs)
	return ContextDoc(**base)


def test_empty():
	assert format_sources([]) == []


def test_removes_duplicates():
	result = format_sources([_doc(), _doc(text="다른 본문")])
	assert len(result) == 1
	assert result[0]["label"] == "물가"
	assert result[0]["report_title"] == "2026 경제전망"


def test_sorted_by_report_then_page():
	docs = [
		_doc(report_id=2, report_title="B", page_start=1, page_end=1),
		_doc(report_id=1, page_start=20, page_end=20),
		_doc(report_id=1, page_start=12, page_end=13),
	]
	result = format_sources(docs)
	assert [(s["report_id"], s["page_start"]) for s in result] == [(1, 12), (1, 20), (2, 1)]


def test_chart_label_and_needs_review():
	doc = _doc(kind="chart", caption="그림 3", page_start=20, page_end=20, needs_review=True)
	result = format_sources([doc])
	assert result[0]["label"] == "그림 3(차트) (추정값)"
	assert result[0]["needs_review"] is True


def test_chart_without_caption():
	doc = _doc(kind="chart", caption=None, page_start=5, page_end=5)
	assert format_sources([doc])[0]["label"] == "그림(차트)"


def test_no_section_title_gives_empty_label():
	assert format_sources([_doc(section_title=None)])[0]["label"] == ""