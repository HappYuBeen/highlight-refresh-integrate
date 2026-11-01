"""지표 DAO 로직 테스트 · 가짜 DB 연결 사용 (장유빈)"""
import pytest

from hri.dao import indicator_dao
from hri.domain.schemas import Indicator


class _FakeCursor:
	"""execute·executemany에 받은 값을 기록하고 정해 둔 행을 돌려주는 가짜 커서."""

	def __init__(self, rows=None, fail=False):
		self.rows = rows or []
		self.fail = fail
		self.executed = None
		self.many = None

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return False

	def execute(self, sql, params=None):
		self.executed = (sql, params)

	def executemany(self, sql, params):
		if self.fail:
			raise RuntimeError("DB 오류")
		self.many = (sql, list(params))
		return len(self.many[1])

	def fetchall(self):
		return self.rows


class _FakeConn:
	"""commit·rollback이 불렸는지 기록하는 가짜 연결."""

	def __init__(self, cursor):
		self.cur = cursor
		self.committed = False
		self.rolled_back = False

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return False

	def cursor(self):
		return self.cur

	def commit(self):
		self.committed = True

	def rollback(self):
		self.rolled_back = True


def _use(monkeypatch, cursor):
	conn = _FakeConn(cursor)
	monkeypatch.setattr(indicator_dao, "get_connection", lambda: conn)
	return conn


def _indicator(**kwargs) -> Indicator:
	base = dict(name="경제성장률", value=2.1, unit="%", period="2027", page=None, source="text", needs_review=False)
	base.update(kwargs)
	return Indicator(**base)


def test_select_converts_rows(monkeypatch):
	row = {
		"report_id": 7, "name": "경제성장률", "value": 2.1, "unit": "%",
		"period": "2027", "page": 12, "source": "chart", "needs_review": 1,
	}
	cursor = _FakeCursor(rows=[row])
	_use(monkeypatch, cursor)
	result = indicator_dao.select_indicators(7)
	assert cursor.executed[1] == (7,)
	assert len(result) == 1
	assert result[0].name == "경제성장률"
	assert result[0].source == "chart"
	assert result[0].needs_review is True
	assert result[0].page == 12



def test_select_empty(monkeypatch):
	_use(monkeypatch, _FakeCursor(rows=[]))
	assert indicator_dao.select_indicators(7) == []


def test_insert_uses_given_report_id_and_commits(monkeypatch):
	cursor = _FakeCursor()
	conn = _use(monkeypatch, cursor)
	items = [_indicator(), _indicator(name="물가상승률", value=2.3, needs_review=True, page=12)]
	assert indicator_dao.insert_indicators(5, items) == 2
	_, rows = cursor.many
	assert rows[0] == (5, "경제성장률", 2.1, "%", "2027", None, "text", 0)
	assert rows[1] == (5, "물가상승률", 2.3, "%", "2027", 12, "text", 1)
	assert conn.committed is True


def test_insert_empty_does_not_connect(monkeypatch):
	def boom():
		raise AssertionError("DB에 접속하면 안 됩니다")

	monkeypatch.setattr(indicator_dao, "get_connection", boom)
	assert indicator_dao.insert_indicators(5, []) == 0


def test_insert_rolls_back_on_error(monkeypatch):
	conn = _use(monkeypatch, _FakeCursor(fail=True))
	with pytest.raises(RuntimeError):
		indicator_dao.insert_indicators(5, [_indicator()])
	assert conn.rolled_back is True
	assert conn.committed is False


def test_select_row_without_page_gives_none(monkeypatch):
	row = {"report_id": 7, "name": "경제성장률", "value": 2.1, "unit": "%", "period": "2027", "page": None, "source": "text", "needs_review": 0}
	_use(monkeypatch, _FakeCursor(rows=[row]))
	assert indicator_dao.select_indicators(7)[0].page is None


def test_sql_uses_page_column(monkeypatch):
	cursor = _FakeCursor()
	_use(monkeypatch, cursor)
	indicator_dao.select_indicators(7)
	assert "page" in cursor.executed[0]
	indicator_dao.insert_indicators(5, [_indicator()])
	assert "page" in cursor.many[0]