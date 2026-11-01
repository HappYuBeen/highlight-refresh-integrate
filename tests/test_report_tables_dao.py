"""select_report_tables 로직 테스트 · 가짜 DB 연결 사용 (장유빈)"""
from hri.dao import report_dao


class _FakeCursor:
	"""execute에 받은 값을 기록하고 정해 둔 행을 돌려주는 가짜 커서."""

	def __init__(self, rows=None):
		self.rows = rows or []
		self.executed = None

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return False

	def execute(self, sql, params=None):
		self.executed = (sql, params)

	def fetchall(self):
		return self.rows


class _FakeConn:
	"""cursor()만 돌려주는 가짜 연결."""

	def __init__(self, cursor):
		self.cur = cursor

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return False

	def cursor(self):
		return self.cur


def _use(monkeypatch, cursor):
	monkeypatch.setattr(report_dao, "get_connection", lambda: _FakeConn(cursor))


def _row(**kwargs) -> dict:
	base = dict(
		doc_id="d1", report_id=7, page=12, caption="[표 1] 성장률", markdown="| a |",
		summary="요약", source_type="table", image_path=None, confidence=None, needs_review=0,
	)
	base.update(kwargs)
	return base


def test_select_converts_rows_to_tables(monkeypatch):
	chart = _row(doc_id="d2", page=20, source_type="chart", image_path="data/charts/raw/a.png", confidence=0.8, needs_review=1)
	cursor = _FakeCursor(rows=[_row(), chart])
	_use(monkeypatch, cursor)
	tables = report_dao.select_report_tables(7)
	assert cursor.executed[1] == (7,)
	assert [t.doc_id for t in tables] == ["d1", "d2"]
	assert tables[0].source_type == "table" and tables[0].needs_review is False
	assert tables[1].source_type == "chart" and tables[1].needs_review is True
	assert tables[1].confidence == 0.8
	assert tables[0].rows == []  # rows는 DB에 저장하지 않으므로 빈 리스트


def test_select_empty(monkeypatch):
	_use(monkeypatch, _FakeCursor(rows=[]))
	assert report_dao.select_report_tables(7) == []


def test_sql_reads_by_report_id_in_page_order(monkeypatch):
	cursor = _FakeCursor()
	_use(monkeypatch, cursor)
	report_dao.select_report_tables(7)
	sql = " ".join(cursor.executed[0].split())
	assert "FROM report_tables" in sql
	assert "WHERE report_id = %s" in sql
	assert "ocr_confidence AS confidence" in sql
	assert "ORDER BY page, doc_id" in sql