"""지표 비교표·차트 로직 테스트 · 가짜 지표·보고서 목록 사용 (장유빈)"""
import html
import re
from pathlib import Path

import pytest

from hri.domain.schemas import Indicator
from hri.service import indicator_service
from hri.service.indicator_service import build_compare_table, plot_indicators


class _R:
	def __init__(self, report_id, title):
		self.report_id, self.title = report_id, title


def _ind(name="경제성장률", value=2.0, unit="%", period="2027", needs_review=False, page=None) -> Indicator:
	return Indicator(name=name, value=value, unit=unit, period=period, source="text", needs_review=needs_review, page=page)


def _use(monkeypatch, by_report: dict, titles: dict | None = None, plot_dir: Path | None = None):
	"""지표 추출·보고서 목록을 가짜로 바꾼다. by_report는 {report_id: [Indicator, ...]}."""
	titles = titles if titles is not None else {rid: f"보고서{rid}" for rid in by_report}
	monkeypatch.setattr(indicator_service, "extract_indicators", lambda rid: by_report.get(rid, []))
	monkeypatch.setattr(indicator_service, "search_reports", lambda keyword="": [_R(rid, t) for rid, t in titles.items()])
	if plot_dir is not None:
		monkeypatch.setattr(indicator_service, "PLOT_DIR", plot_dir)

class _RF(_R):
	def __init__(self, report_id, title, file_path):
		super().__init__(report_id, title)
		self.file_path = file_path


def _lines(table: str) -> list[str]:
	"""HTML 비교 표를 읽기 쉬운 '| 칸 | 칸 |' 줄로 바꾼다 (머리글 다음에 '| --- |' 줄, 표 아래 문구는 그대로)."""
	lines = []
	for line in table.splitlines():
		cells = [html.unescape(c) for c in re.findall(r"<t[hd](?: [^>]*)?>(.*?)</t[hd]>", line)]
		if line.startswith("<thead>"):
			lines += ["| " + " | ".join(cells) + " |", "| " + " | ".join("---" for _ in cells) + " |"]
		elif line.startswith("<tr>"):
			lines.append("| " + " | ".join(cells) + " |")
		elif line and not line.startswith("<"):
			lines.append(line)
	return lines


def _text(table: str) -> str:
	return "\n".join(_lines(table))


# ---------------- build_compare_table ----------------
def test_table_pivots_by_name_period_unit(monkeypatch):
	_use(monkeypatch, {1: [_ind(value=2.0)], 2: [_ind(value=2.4)]}, {1: "A", 2: "B"})
	assert _lines(build_compare_table([1, 2])) == [
		"| 지표 | 시점 | 단위 | A | B | 차이(최대−최소) |",
		"| --- | --- | --- | --- | --- | --- |",
		"| 경제성장률 | 2027 | % | 2 | 2.4 | 0.4%p |",
	]


def test_missing_cells_are_dash(monkeypatch):
	_use(monkeypatch, {1: [_ind(), _ind("실업률", 3.1)], 2: [_ind(value=2.4)]}, {1: "A", 2: "B"})
	lines = _lines(build_compare_table([1, 2]))
	assert lines[-1] == "| 실업률 | 2027 | % | 3.1 | - | - |"


def test_shared_rows_come_first(monkeypatch):
	_use(monkeypatch, {1: [_ind("가나다", 1.0), _ind("실업률", 3.1)], 2: [_ind("실업률", 3.3)]}, {1: "A", 2: "B"})
	names = [line.split(" | ")[0].strip("| ") for line in _lines(build_compare_table([1, 2]))[2:]]
	assert names == ["실업률", "가나다"]


def test_needs_review_gets_star_and_note(monkeypatch):
	_use(monkeypatch, {1: [_ind(value=1390.0, unit="원", needs_review=True)], 2: [_ind(value=1380.0, unit="원")]}, {1: "A", 2: "B"})
	table = build_compare_table([1, 2])
	assert "| 1,390* | 1,380 |" in _text(table)
	assert table.splitlines()[-1].startswith("*")


def test_no_note_without_review(monkeypatch):
	_use(monkeypatch, {1: [_ind()], 2: [_ind()]})
	assert "*" not in _text(build_compare_table([1, 2]))


def test_same_unit_different_period_are_different_rows(monkeypatch):
	_use(monkeypatch, {1: [_ind(period="2027"), _ind(period="2028", value=2.2)], 2: [_ind(period="2027")]})
	body = _lines(build_compare_table([1, 2]))[2:]
	assert [line.split(" | ")[1] for line in body] == ["2027", "2028"]


def test_first_value_wins_when_report_repeats_a_row(monkeypatch):
	_use(monkeypatch, {1: [_ind(value=2.0), _ind(value=2.5)], 2: [_ind()]})
	assert "| 2 | 2 |" in _text(build_compare_table([1, 2]))


def test_duplicate_titles_get_report_id(monkeypatch):
	_use(monkeypatch, {1: [_ind()], 2: [_ind()]}, {1: "같은 제목", 2: "같은 제목"})
	assert _lines(build_compare_table([1, 2]))[0] == "| 지표 | 시점 | 단위 | 같은 제목 (#1) | 같은 제목 (#2) | 차이(최대−최소) |"


def test_unknown_report_gets_fallback_title(monkeypatch):
	_use(monkeypatch, {1: [_ind()], 2: [_ind()]}, {1: "A"})
	assert _lines(build_compare_table([1, 2]))[0].endswith("| A | 보고서 #2 | 차이(최대−최소) |")


def test_pipe_in_title_is_kept(monkeypatch):
	_use(monkeypatch, {1: [_ind()], 2: [_ind()]}, {1: "A|B", 2: "C"})
	assert "| A|B | C |" in _text(build_compare_table([1, 2]))


def test_duplicate_report_ids_are_merged(monkeypatch):
	_use(monkeypatch, {1: [_ind()]}, {1: "A"})
	assert _lines(build_compare_table([1, 1]))[0] == "| 지표 | 시점 | 단위 | A |"


def test_empty_unit_is_dash(monkeypatch):
	_use(monkeypatch, {1: [_ind(unit="")], 2: [_ind(unit="")]})
	assert "| 경제성장률 | 2027 | - |" in _text(build_compare_table([1, 2]))


def test_no_indicators_returns_empty_string(monkeypatch):
	_use(monkeypatch, {})
	assert build_compare_table([1, 2]) == ""


@pytest.mark.parametrize("value, expected", [(1390.0, "1,390"), (2.5, "2.5"), (-0.3, "-0.3"), (0.0, "0"), (1234567.891, "1,234,567.89"), (-0.001, "0")])
def test_format_value(value, expected):
	assert indicator_service._format_value(value) == expected


# ---------------- plot_indicators ----------------
def _png(path: str) -> bytes:
	data = Path(path).read_bytes()
	assert data[:8] == b"\x89PNG\r\n\x1a\n"
	return data


def test_plot_writes_png_with_uuid_name(monkeypatch, tmp_path):
	_use(monkeypatch, {1: [_ind(value=2.0)], 2: [_ind(value=2.4)]}, plot_dir=tmp_path)
	first, second = plot_indicators([1, 2]), plot_indicators([1, 2])
	assert Path(first).parent == tmp_path and Path(first).name.startswith("compare_") and first.endswith(".png")
	assert first != second
	_png(first)


def test_plot_returns_none_without_shared_indicators(monkeypatch, tmp_path):
	_use(monkeypatch, {1: [_ind("가")], 2: [_ind("나")]}, plot_dir=tmp_path)
	assert plot_indicators([1, 2]) is None
	assert list(tmp_path.iterdir()) == []


def test_plot_returns_none_without_any_indicators(monkeypatch, tmp_path):
	_use(monkeypatch, {}, plot_dir=tmp_path)
	assert plot_indicators([1, 2]) is None


def test_plot_handles_units_review_negative_and_many_rows(monkeypatch, tmp_path):
	items1 = [_ind(f"지표{i}", float(i), "%", "2027") for i in range(12)] + [_ind("환율", 1390.0, "원", "2027Q1", needs_review=True), _ind("수지", -5.0, "억 원", "2027")]
	items2 = [_ind(f"지표{i}", float(i) + 0.5, "%", "2027") for i in range(12)] + [_ind("환율", 1380.0, "원", "2027Q1"), _ind("수지", 3.0, "억 원", "2027")]
	_use(monkeypatch, {1: items1, 2: items2, 3: items1[:2]}, plot_dir=tmp_path)
	_png(plot_indicators([1, 2, 3]))


def test_plot_limits_unit_groups(monkeypatch, tmp_path):
	units = ["%", "원", "억 원", "조 원", "%p"]
	by_report = {rid: [_ind(f"지표{u}", 1.0 + rid, u) for u in units] for rid in (1, 2)}
	_use(monkeypatch, by_report, plot_dir=tmp_path)
	_png(plot_indicators([1, 2]))  # 오류 없이 MAX_UNIT_GROUPS개 칸만 그린다


def test_korean_font_comes_from_assets_fonts(monkeypatch, tmp_path):
	fonts = tmp_path / "fonts"
	fonts.mkdir()
	real = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "NanumGothic.ttf"
	if not real.exists():
		pytest.skip("assets/fonts에 한글 폰트 파일이 없습니다")
	(fonts / "NanumGothic.ttf").write_bytes(real.read_bytes())
	monkeypatch.setattr(indicator_service, "FONT_DIR", fonts)
	assert indicator_service._use_korean_font() == "NanumGothic"


def test_missing_font_dir_does_not_break(monkeypatch, tmp_path):
	monkeypatch.setattr(indicator_service, "FONT_DIR", tmp_path / "none")
	monkeypatch.setattr(indicator_service, "SYSTEM_FONT_NAMES", ["이런이름의폰트는없다"])
	assert indicator_service._use_korean_font() is None


# ---------------- 출처 쪽 ----------------
def test_page_is_shown_next_to_value(monkeypatch):
	_use(monkeypatch, {1: [_ind(value=2.0, page=12)], 2: [_ind(value=2.4, page=3)]})
	assert _lines(build_compare_table([1, 2]))[-1] == "| 경제성장률 | 2027 | % | 2 (p.12) | 2.4 (p.3) | 0.4%p |"


def test_page_is_omitted_when_unknown(monkeypatch):
	_use(monkeypatch, {1: [_ind(value=2.0, page=12)], 2: [_ind(value=2.4)]})
	assert "| 2 (p.12) | 2.4 |" in _text(build_compare_table([1, 2]))


def test_page_goes_after_review_star(monkeypatch):
	_use(monkeypatch, {1: [_ind(value=1390.0, unit="원", needs_review=True, page=7)], 2: [_ind(value=1380.0, unit="원")]})
	assert "| 1,390* (p.7) | 1,380 |" in _text(build_compare_table([1, 2]))


def test_page_comes_from_the_value_that_wins(monkeypatch):
	_use(monkeypatch, {1: [_ind(value=2.0, page=5), _ind(value=2.5, page=9)], 2: [_ind()]})
	assert "| 2 (p.5) |" in _text(build_compare_table([1, 2]))


# ---------------- 차이 열 ----------------
def test_diff_of_percent_is_percent_point(monkeypatch):
	_use(monkeypatch, {1: [_ind(value=2.0)], 2: [_ind(value=1.7)]})
	assert _lines(build_compare_table([1, 2]))[-1].endswith("| 2 | 1.7 | 0.3%p |")


def test_diff_keeps_other_units(monkeypatch):
	_use(monkeypatch, {1: [_ind("환율", 1390.0, "원", "2027Q1")], 2: [_ind("환율", 1362.5, "원", "2027Q1")]})
	assert _lines(build_compare_table([1, 2]))[-1].endswith("| 1,390 | 1,362.5 | 27.5원 |")


def test_diff_with_empty_unit_has_no_suffix(monkeypatch):
	_use(monkeypatch, {1: [_ind("지수", 100.0, "")], 2: [_ind("지수", 98.0, "")]})
	assert _lines(build_compare_table([1, 2]))[-1].endswith("| 100 | 98 | 2 |")


def test_diff_is_max_minus_min_for_three_reports(monkeypatch):
	_use(monkeypatch, {1: [_ind(value=2.0)], 2: [_ind(value=2.4)], 3: [_ind(value=1.5)]})
	assert _lines(build_compare_table([1, 2, 3]))[-1].endswith("| 2 | 2.4 | 1.5 | 0.9%p |")


def test_diff_ignores_reports_without_the_row(monkeypatch):
	_use(monkeypatch, {1: [_ind(value=2.0)], 2: [_ind(value=2.4)], 3: []})
	assert _lines(build_compare_table([1, 2, 3]))[-1].endswith("| 2 | 2.4 | - | 0.4%p |")


def test_diff_dash_when_only_one_report_has_value(monkeypatch):
	_use(monkeypatch, {1: [_ind(), _ind("실업률", 3.1)], 2: [_ind(value=2.4)]})
	assert _lines(build_compare_table([1, 2]))[-1].endswith("| 3.1 | - | - |")


def test_diff_gets_star_when_a_value_needs_review(monkeypatch):
	_use(monkeypatch, {1: [_ind(value=2.0, needs_review=True)], 2: [_ind(value=2.4)]})
	table = build_compare_table([1, 2])
	assert "| 2* | 2.4 | 0.4%p* |" in _text(table)
	assert table.splitlines()[-1].startswith("*")


def test_single_report_has_no_diff_column(monkeypatch):
	_use(monkeypatch, {1: [_ind()]}, {1: "A"})
	assert _lines(build_compare_table([1]))[0] == "| 지표 | 시점 | 단위 | A |"


# ---------------- 열 머리글 = 보고서 선택 창의 이름 ----------------
def _use_files(monkeypatch, files: dict):
	"""보고서 목록을 (제목, 저장 경로)가 있는 가짜로 바꾼다. files는 {report_id: (title, file_path)}."""
	_use(monkeypatch, {rid: [_ind()] for rid in files})
	monkeypatch.setattr(indicator_service, "search_reports", lambda keyword="": [_RF(rid, t, p) for rid, (t, p) in files.items()])


def test_header_shows_title_without_file_name(monkeypatch):
	_use_files(monkeypatch, {1: ("2026 경제전망", "data/raw/a.pdf"), 2: ("2027 경제전망", "data/raw/b.pdf")})
	assert _lines(build_compare_table([1, 2]))[0] == "| 지표 | 시점 | 단위 | 2026 경제전망 | 2027 경제전망 | 차이(최대−최소) |"


def test_same_title_gets_report_id(monkeypatch):
	_use_files(monkeypatch, {1: ("같은 제목", "data/raw/a.pdf"), 2: ("같은 제목", "data/raw/b.pdf")})
	assert _lines(build_compare_table([1, 2]))[0] == "| 지표 | 시점 | 단위 | 같은 제목 (#1) | 같은 제목 (#2) | 차이(최대−최소) |"


def test_html_in_title_is_escaped(monkeypatch):
	_use_files(monkeypatch, {1: ("<b>x</b>", "data/raw/a.pdf"), 2: ("B", "data/raw/b.pdf")})
	table = build_compare_table([1, 2])
	assert "<b>" not in table and "&lt;b&gt;x&lt;/b&gt;" in table


# ---------------- 보고서 이름 길이 · 열 너비 · 공통 지표 정렬 ----------------
def test_long_title_is_cut_to_12_chars_and_dots(monkeypatch):
	_use(monkeypatch, {1: [_ind()], 2: [_ind()]}, {1: "가" * 15, 2: "나" * 16})
	assert _lines(build_compare_table([1, 2]))[0] == f"| 지표 | 시점 | 단위 | {'가' * 15} | {'나' * 12}... | 차이(최대−최소) |"


def test_cut_title_keeps_full_name_as_tooltip(monkeypatch):
	_use(monkeypatch, {1: [_ind()], 2: [_ind()]}, {1: "나" * 16, 2: "B"})
	assert f'title="{"나" * 16}"' in build_compare_table([1, 2])


def test_same_cut_title_gets_report_id(monkeypatch):
	_use(monkeypatch, {1: [_ind()], 2: [_ind()]}, {1: "가" * 12 + "끝1번째", 2: "가" * 12 + "끝2번째"})
	assert f"| {'가' * 12}... (#1) | {'가' * 12}... (#2) |" in _lines(build_compare_table([1, 2]))[0]


def _col_widths(table: str) -> list[float]:
	return [float(w) for w in re.findall(r'<col style="width:([\d.]+)em">', table)]


def test_report_columns_have_same_width(monkeypatch):
	_use(monkeypatch, {1: [_ind()], 2: [_ind()]}, {1: "A", 2: "아주 긴 보고서 이름입니다 정말로 깁니다"})
	widths = _col_widths(build_compare_table([1, 2]))
	assert widths[3] == widths[4]


def test_name_column_fits_longest_name(monkeypatch):
	_use(monkeypatch, {1: [_ind("가나"), _ind("가나다라")], 2: [_ind("가나")]})
	widths = _col_widths(build_compare_table([1, 2]))
	assert 4 < widths[0] < 0.2 * sum(widths)


def test_name_column_is_at_most_20_percent(monkeypatch):
	_use(monkeypatch, {1: [_ind("가" * 80)], 2: [_ind("가" * 80)]})
	widths = _col_widths(build_compare_table([1, 2]))
	assert widths[0] == pytest.approx(0.2 * sum(widths), abs=0.2)


def test_indicator_shared_in_other_period_comes_before_single(monkeypatch):
	_use(monkeypatch, {1: [_ind("가나다", period="2027"), _ind("실업률", period="2027")], 2: [_ind("실업률", period="2028")]})
	names = [line.split(" | ")[0].strip("| ") for line in _lines(build_compare_table([1, 2]))[2:]]
	assert names == ["실업률", "실업률", "가나다"]


def test_report_titles_have_no_file_name(monkeypatch):
	_use_files(monkeypatch, {1: ("A", "data/raw/a.pdf"), 2: ("B", "data/raw/b.pdf")})
	assert indicator_service._report_titles([1, 2]) == {1: "A", 2: "B"}

def test_plot_axes_share_slot_count_and_bar_width(monkeypatch, tmp_path):
	"""지표 수가 다른 칸도 가로축 자리 수가 같아 막대 두께가 같다 (적은 칸은 빈칸이 남는다)."""
	from matplotlib.figure import Figure

	saved = []
	monkeypatch.setattr(Figure, "savefig", lambda self, path, **k: saved.append(self))
	items = lambda base: [_ind(f"지표{i}", base + i, "%") for i in range(6)] + [_ind("환율", 1390.0 + base, "원")]
	_use(monkeypatch, {1: items(1.0), 2: items(1.5)}, plot_dir=tmp_path)
	plot_indicators([1, 2])
	axes = saved[0].axes
	assert axes[0].get_xlim() == axes[1].get_xlim() == (-0.5, 5.5)
	widths = {round(p.get_width(), 6) for ax in axes for p in ax.patches}
	assert widths == {indicator_service.MAX_BAR_WIDTH}


def test_plot_keeps_min_slots_with_few_indicators(monkeypatch, tmp_path):
	from matplotlib.figure import Figure

	saved = []
	monkeypatch.setattr(Figure, "savefig", lambda self, path, **k: saved.append(self))
	_use(monkeypatch, {1: [_ind(value=2.0)], 2: [_ind(value=2.4)]}, plot_dir=tmp_path)
	plot_indicators([1, 2])
	assert saved[0].axes[0].get_xlim() == (-0.5, indicator_service.MIN_BAR_SLOTS - 0.5)
