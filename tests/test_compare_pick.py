"""보고서 선택 창 테스트 · 목록·선택 칸·최대 선택 수 (장유빈)"""
from hri.view import indicator_view
from hri.view.indicator_view import (
	MAX_COMPARE_REPORTS,
	PICK_LIMIT_MESSAGE,
	load_compare_reports,
	new_pick,
	on_pick_change,
	reset_compare_reports,
)


class _R:
	def __init__(self, report_id, title):
		self.report_id, self.title = report_id, title


def _use_reports(monkeypatch, count: int):
	"""보고서 목록을 가짜 count개로 바꾸고 search_reports가 받은 user_id를 기록한다."""
	seen = []

	def fake_search(keyword="", limit=None, user_id=None):
		seen.append(user_id)
		return [_R(i, f"보고서{i}") for i in range(1, count + 1)]

	monkeypatch.setattr(indicator_view, "search_reports", fake_search)
	return seen


USER = {"user": {"user_id": "kim", "role": "user"}}


def test_shows_all_reports_without_paging(monkeypatch):
	_use_reports(monkeypatch, 23)
	pick, checkbox = reset_compare_reports(USER)
	assert [rid for _, rid in checkbox["choices"]] == list(range(1, 24))
	assert checkbox["value"] == []


def test_normal_user_sees_only_own_reports(monkeypatch):
	seen = _use_reports(monkeypatch, 3)
	reset_compare_reports(USER)
	assert seen == ["kim"]


def test_selection_keeps_click_order(monkeypatch):
	_use_reports(monkeypatch, 23)
	pick = on_pick_change([3], reset_compare_reports(USER)[0])[0]
	pick, checkbox = on_pick_change([1, 3], pick)
	assert pick["selected"] == [3, 1]  # 고른 순서 = 비교 표 열 순서
	assert checkbox["value"] == [3, 1]


def test_uncheck_removes_only_that_report(monkeypatch):
	_use_reports(monkeypatch, 23)
	pick = on_pick_change([1, 2], reset_compare_reports(USER)[0])[0]
	pick = on_pick_change([2], pick)[0]
	assert pick["selected"] == [2]


def test_more_than_max_is_rejected(monkeypatch):
	_use_reports(monkeypatch, 23)
	warned = []
	monkeypatch.setattr(indicator_view.gr, "Warning", lambda m, *a, **k: warned.append(m))
	pick, checkbox = on_pick_change([1, 2, 3, 4, 5], reset_compare_reports(USER)[0])
	pick, checkbox = on_pick_change([1, 2, 3, 4, 5, 6], pick)
	assert pick["selected"] == [1, 2, 3, 4, 5]
	assert checkbox["value"] == [1, 2, 3, 4, 5]
	assert warned == [PICK_LIMIT_MESSAGE]
	assert MAX_COMPARE_REPORTS == 5  # COMPARE_TABLE_STYLE의 비활성화 선택자(label.selected 5개)와 맞아야 한다


def test_reload_keeps_selection_still_in_list(monkeypatch):
	_use_reports(monkeypatch, 5)
	pick = on_pick_change([4, 5], reset_compare_reports(USER)[0])[0]
	_use_reports(monkeypatch, 4)  # 보고서 5가 사라짐
	pick = load_compare_reports(USER, pick)[0]
	assert pick["selected"] == [4]


def test_list_error_shows_empty_list(monkeypatch):
	def broken(*a, **k):
		raise RuntimeError("DB 오류")

	monkeypatch.setattr(indicator_view, "search_reports", broken)
	pick, checkbox = load_compare_reports(USER, new_pick())
	assert checkbox["choices"] == [] and checkbox["value"] == []
