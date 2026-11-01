"""지표 조회·저장 · select_indicators, insert_indicators (장유빈)"""

from hri.dao.db import get_connection
from hri.domain.schemas import Indicator


# =====================================================================
# [장유빈] 지표 조회·저장 · select_indicators, insert_indicators
# 보고서에서 추출한 지표를 indicators 테이블에 저장하고, 같은 보고서를 다시 추출하지 않게 읽어 온다
# =====================================================================
def select_indicators(report_id: int) -> list[Indicator]:
	"""indicators 테이블에서 report_id 보고서의 지표 목록을 저장된 순서대로 조회한다.

	저장된 지표가 없으면 빈 리스트를 돌려준다. needs_review는 DB의 0/1을 bool로 바꾼다.
	"""
	sql = """
		SELECT report_id, name, value, unit, period, page, source, needs_review
		FROM indicators
		WHERE report_id = %s
		ORDER BY indicator_id
	"""
	with get_connection() as conn:
		with conn.cursor() as cur:
			cur.execute(sql, (report_id,))
			rows = cur.fetchall()
	# DictCursor가 준 행(dict)을 Indicator로 바꾼다
	return [Indicator(**row) for row in rows]


def insert_indicators(report_id: int, indicators: list[Indicator]) -> int:
	"""extract_indicators() 결과를 indicators 테이블에 한 번에 INSERT하고 저장한 행 수를 돌려준다.

	Indicator.report_id가 비어 있어도 되도록 저장할 report_id는 인자로 받은 값을 쓴다.
	지표가 없으면 DB에 접속하지 않고 0을 돌려주고, 실패하면 rollback한 뒤 예외를 그대로 던진다.
	"""
	# 저장할 게 없으면 DB에 접속하지 않는다
	if not indicators:
		return 0

	sql = """
		INSERT INTO indicators (report_id, name, value, unit, period, page, source, needs_review)
		VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
	"""
	# 지표마다 (report_id, name, ..., page, ...) 순서의 튜플로 바꾼다. page는 모르면 None(NULL). needs_review는 DB의 BOOLEAN에 맞춰 0/1
	rows = [
		(report_id, i.name, i.value, i.unit, i.period, i.page, i.source, int(bool(i.needs_review)))
		for i in indicators
	]

	with get_connection() as conn:
		try:
			with conn.cursor() as cur:
				# executemany: 같은 INSERT를 여러 행에 한 번에 실행한다
				count = cur.executemany(sql, rows)
			# 연결이 autocommit이 꺼져 있으므로 저장을 확정하려면 commit이 필요하다
			conn.commit()
		except Exception:
			# 중간에 실패하면 이미 넣은 행도 되돌린다
			conn.rollback()
			raise
	return count