"""보고서 목록 사용자별 필터 테스트 · owner_filter, on_report_search (장유빈)"""
from datetime import date
from types import SimpleNamespace

from hri.service.report_service import owner_filter
from hri.view import report_view


def _report(report_id: int, title: str) -> SimpleNamespace:
	return SimpleNamespace(
		report_id=report_id,
		title=title,
		publisher="현대경제연구원",
		published_date=date(2026, 1, 1),
		chunk_count=3,
	)


def test_owner_filter_admin_sees_all():
	assert owner_filter({"user_id": "admin", "role": "admin"}) is None


def test_owner_filter_user_sees_own():
	assert owner_filter({"user_id": "user", "role": "user"}) == "user"


def test_owner_filter_user_object():
	assert owner_filter(SimpleNamespace(user_id="kim", role="user")) == "kim"


def test_owner_filter_not_logged_in_sees_nothing():
	assert owner_filter(None) == ""


def test_on_report_search_passes_user_id(monkeypatch):
	calls = []

	def fake_search(keyword="", limit=None, user_id=None):
		calls.append((keyword, user_id))
		return [_report(1, "물가 보고서")]

	monkeypatch.setattr(report_view, "search_reports", fake_search)
	state = {"user": {"user_id": "user", "role": "user"}}
	rows, ids, message = report_view.on_report_search("물가", state)
	assert calls == [("물가", "user")]
	assert ids == [1]
	assert message == "총 1건"


def test_on_report_search_admin_gets_all(monkeypatch):
	calls = []

	def fake_search(keyword="", limit=None, user_id=None):
		calls.append(user_id)
		return []

	monkeypatch.setattr(report_view, "search_reports", fake_search)
	state = {"user": {"user_id": "admin", "role": "admin"}}
	report_view.on_report_search("", state)
	assert calls == [None]


def test_on_report_search_without_login_filters_everything(monkeypatch):
	calls = []

	def fake_search(keyword="", limit=None, user_id=None):
		calls.append(user_id)
		return []

	monkeypatch.setattr(report_view, "search_reports", fake_search)
	report_view.on_report_search("", None)
	assert calls == [""]