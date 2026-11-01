"""대시보드 SQL · select_top_questions, select_source_ratio, select_perf_cost, select_avg_response_ms, select_recent_errors, select_daily_counts, select_persona_ratio, select_feedback_ratio, select_no_source_questions, select_route_ratio (임세준)

관리자 대시보드 화면에 보여 줄 값을 qa_logs·llm_usage 테이블에서 집계하는 DAO.
1차: get_dashboard_data()(3.7.8)가 이 함수들의 결과를 화면 형식으로 가공한다.
- select_top_questions(): 많이 물어본 질문 TOP 10
- select_source_ratio(): '자료에 없습니다'(no_answer) 응답 비율
- select_perf_cost(): 일자별 평균 응답 시간 + flow·모델별 토큰 합계 (원화 비용은 DB에 저장하지 않고 get_dashboard_data()에서 계산)
- select_avg_response_ms(): 전체 답변의 평균 응답 시간 (잡담·오류 제외)
- select_recent_errors(): 최근 에러 기록 20건
2차: get_dashboard_data_v2()(3.7.11)가 이 함수들의 결과를 화면 형식으로 가공한다.
- select_daily_counts(): 날짜별 질문 수·평균 응답 시간 (평균 응답 시간은 잡담·오류 제외)
- select_persona_ratio(): 페르소나별 질문 수 + 합계 (WITH ROLLUP)
- select_feedback_ratio(): 👍/👎 건수·합계·비율 (WITH ROLLUP)
- select_no_source_questions(): '자료에 없습니다'가 2회 이상 나온 질문 (보고서 보강 후보)
- select_route_ratio(): 질문 처리 방식(route)별 질문 수 + 합계 (WITH ROLLUP)
"""
from hri.dao.db import get_connection


def select_top_questions() -> list[dict]:
	"""
	질문 TOP 10 조회

	qa_logs를 question으로 GROUP BY 하여 COUNT 내림차순 상위 10건을 가져온다.
	(같은 질문을 몇 번 했는지 세서 많이 나온 순서로 10개)
	- 횟수가 같으면 가장 최근에 물어본 날짜(MAX(created_at))가 늦은 질문이 먼저 온다
	  (10위 경계에서 같은 횟수인 질문이 여럿이어도 매번 같은 질문이 보이도록 순서를 고정)

	입력: 없음
	출력: list[dict] (예: [{'question': '기준금리 전망은?', 'count': 5}, ...], 기록이 없으면 [])
	"""
	sql = """
		SELECT question, COUNT(*) AS count
		FROM qa_logs
		GROUP BY question
		ORDER BY count DESC, MAX(created_at) DESC
		LIMIT 10
	"""

	with get_connection() as conn:
		cursor = conn.cursor()
		cursor.execute(sql)
		rows = cursor.fetchall()

	# fetchall()이 튜플로 줄 수 있어서 리스트로 바꿔서 돌려줌
	return list(rows)


def select_source_ratio() -> float:
	"""
	자료 유무 응답 비율 조회 ('자료에 없습니다' 응답 비율)

	route IN ('rag', 'no_answer')인 답변 중 route = 'no_answer'의 비율을
	AVG(CASE WHEN ...)로 계산한다. (잡담(chat)·오류(error)는 제외)
	- no_answer면 1, rag면 0으로 바꾼 뒤 평균을 내면 no_answer의 비율이 된다
	  예) rag 3개 + no_answer 1개 → (0+0+0+1) / 4 = 0.25

	입력: 없음
	출력: float (0.0 ~ 1.0, 소수점 넷째 자리까지, 해당 답변이 하나도 없으면 0.0)
	"""
	sql = """
		SELECT AVG(CASE WHEN route = 'no_answer' THEN 1 ELSE 0 END) AS ratio
		FROM qa_logs
		WHERE route IN ('rag', 'no_answer')
	"""

	with get_connection() as conn:
		cursor = conn.cursor()
		cursor.execute(sql)
		row = cursor.fetchone()

	# 해당 답변이 하나도 없으면 AVG 결과가 NULL(None)
	if row["ratio"] == None:
		return 0.0

	return float(row["ratio"])


def select_perf_cost() -> list[dict]:
	"""
	응답 시간·토큰 조회 (원화 비용 계산용 재료)

	일자별 평균 응답 시간(qa_logs AVG)과 llm_usage의 flow·모델별 토큰 합계(SUM)를 집계한다.
	원화 비용은 DB에 저장하지 않으므로 여기서는 토큰만 모으고,
	get_dashboard_data()가 cost_korean_won()으로 원화 비용을 계산해 대시보드에만 보여 준다.

	순서
	1. llm_usage를 날짜·flow·모델별로 묶어 입력 토큰·출력 토큰을 SUM 한다 (u)
	   (모델마다 단가가 달라서 모델별로 나눠 둬야 원화 비용을 계산할 수 있음)
	2. qa_logs를 날짜별로 묶어 응답 시간을 AVG 한다 (q)
	3. 두 결과를 날짜로 LEFT JOIN 해서 한 줄에 함께 담는다
	   (질문이 없던 날은 평균 응답 시간이 None)
	4. 최근 날짜부터, 같은 날은 flow 순서(qa → ingest → summary → agent, DB ENUM 순서)·모델 이름 순으로 정렬한다
	5. DB가 돌려준 Decimal(소수 타입) 값을 int·float로 바꿔서 돌려준다

	입력: 없음
	출력: list[dict]
	      예) [{'day': date(2026, 10, 28), 'flow': 'qa', 'model': 'gpt-5-mini-2025-08-07',
	            'input_tokens': 1200, 'output_tokens': 300, 'avg_response_ms': 1534.5}, ...]
	"""
	sql = """
		SELECT u.day, u.flow, u.model, u.input_tokens, u.output_tokens, q.avg_response_ms
		FROM (
			SELECT DATE(created_at) AS day, flow, model,
				SUM(input_tokens) AS input_tokens,
				SUM(output_tokens) AS output_tokens
			FROM llm_usage
			GROUP BY DATE(created_at), flow, model
		) AS u
		LEFT JOIN (
			SELECT DATE(created_at) AS day, AVG(response_ms) AS avg_response_ms
			FROM qa_logs
			GROUP BY DATE(created_at)
		) AS q
		ON u.day = q.day
		ORDER BY u.day DESC, u.flow, u.model
	"""

	with get_connection() as conn:
		cursor = conn.cursor()
		cursor.execute(sql)
		rows = cursor.fetchall()

	# Decimal → int·float로 바꾸기
	results = []
	for row in rows:
		avg_response_ms = row["avg_response_ms"]
		if avg_response_ms != None:
			avg_response_ms = round(float(avg_response_ms), 1)

		results.append({
			"day": row["day"],
			"flow": row["flow"],
			"model": row["model"],
			"input_tokens": int(row["input_tokens"]),
			"output_tokens": int(row["output_tokens"]),
			"avg_response_ms": avg_response_ms
		})

	return results


def select_avg_response_ms() -> float:
	"""
	평균 응답 시간 조회 (전체 답변 기준)

	qa_logs에서 route IN ('rag', 'no_answer')인 답변의 응답 시간을 AVG 한다. (잡담(chat)·오류(error)는 제외)
	- 일자별 평균을 다시 평균 내지 않고, 답변 하나하나를 똑같은 무게로 평균 낸다
	- 응답 시간이 비어 있는(NULL) 기록은 AVG에서 빠진다

	입력: 없음
	출력: float (ms, 소수점 첫째 자리까지, 해당 답변이 하나도 없으면 0.0)
	"""
	sql = """
		SELECT AVG(response_ms) AS avg_response_ms
		FROM qa_logs
		WHERE route IN ('rag', 'no_answer')
	"""

	with get_connection() as conn:
		cursor = conn.cursor()
		cursor.execute(sql)
		row = cursor.fetchone()

	# 해당 답변이 하나도 없으면 AVG 결과가 NULL(None)
	if row["avg_response_ms"] == None:
		return 0.0

	return round(float(row["avg_response_ms"]), 1)


def select_recent_errors() -> list[dict]:
	"""
	최근 에러 로그 조회

	route = 'error'이거나 error 컬럼이 NULL이 아닌 기록을 최신순 20건 가져온다.
	(같은 시각에 저장된 기록은 나중에 저장된 것(log_id가 큰 것)이 먼저 오게 함)

	입력: 없음
	출력: list[dict] (예: [{'log_id': 7, 'user_id': 'kim', 'question': '', 'route': 'error',
	                       'error': '빈 질문', 'created_at': datetime(...)}, ...], 없으면 [])
	"""
	sql = """
		SELECT log_id, user_id, question, route, error, created_at
		FROM qa_logs
		WHERE route = 'error' OR error IS NOT NULL
		ORDER BY created_at DESC, log_id DESC
		LIMIT 20
	"""

	with get_connection() as conn:
		cursor = conn.cursor()
		cursor.execute(sql)
		rows = cursor.fetchall()

	return list(rows)



def select_daily_counts() -> list[dict]:
	"""
	사용 추이 조회 (날짜별 질문 수·평균 응답 시간)

	qa_logs를 DATE(created_at)별로 묶어 질문 수와 평균 응답 시간을 집계한다.
	- 질문 수는 모든 질문을 센다 (잡담(chat)·오류(error) 포함)
	- 평균 응답 시간은 rag·no_answer 답변만 평균 낸다 (잡담(chat)·오류(error)는 제외)
	- 차트를 그리기 좋게 오래된 날짜부터 정렬한다
	- DB가 돌려준 Decimal(소수 타입) 평균 응답 시간을 float로 바꾼다 (소수 첫째 자리까지)

	입력: 없음
	출력: list[dict]
	      예) [{'day': date(2026, 10, 27), 'question_count': 12, 'avg_response_ms': 1000.0}, ...]
	      (기록이 없으면 [], 그날 rag·no_answer 답변의 응답 시간이 하나도 없으면 avg_response_ms는 None)
	"""
	sql = """
		SELECT DATE(created_at) AS day,
			COUNT(*) AS question_count,
			AVG(CASE WHEN route IN ('rag', 'no_answer') THEN response_ms END) AS avg_response_ms
		FROM qa_logs
		GROUP BY DATE(created_at)
		ORDER BY day
	"""

	with get_connection() as conn:
		cursor = conn.cursor()
		cursor.execute(sql)
		rows = cursor.fetchall()

	# Decimal → float로 바꾸기
	results = []
	for row in rows:
		avg_response_ms = row["avg_response_ms"]
		if avg_response_ms != None:
			avg_response_ms = round(float(avg_response_ms), 1)

		results.append({
			"day": row["day"],
			"question_count": int(row["question_count"]),
			"avg_response_ms": avg_response_ms
		})

	return results


def select_persona_ratio() -> list[dict]:
	"""
	페르소나별 질문 수 조회 (합계 행 포함)

	persona별 COUNT를 GROUP BY ... WITH ROLLUP으로 집계해 합계 행까지 한 번에 조회한다.
	- WITH ROLLUP은 GROUP BY 결과 맨 아래에 전체 합계 줄을 하나 더 붙여 준다
	- 합계 줄은 persona가 None이다 (어느 페르소나에도 속하지 않는 '전체'라서)
	  예) 입문자 3 + 실무자 9 → [{'persona': '입문자', 'count': 3}, {'persona': '실무자', 'count': 9}, {'persona': None, 'count': 12}]
	- 비율(각 페르소나 수 / 합계)은 get_dashboard_data_v2()가 이 결과로 계산한다

	입력: 없음
	출력: list[dict] (페르소나별 줄 + 맨 아래 합계 줄, 기록이 없으면 [])
	"""
	sql = """
		SELECT persona, COUNT(*) AS count
		FROM qa_logs
		GROUP BY persona WITH ROLLUP
	"""

	with get_connection() as conn:
		cursor = conn.cursor()
		cursor.execute(sql)
		rows = cursor.fetchall()

	return list(rows)


def select_feedback_ratio() -> dict:
	"""
	피드백 비율 조회 (👍/👎)

	피드백이 있는 답변만 골라(WHERE feedback IS NOT NULL) feedback별 COUNT를 GROUP BY ... WITH ROLLUP으로 집계해
	👍(1)·👎(-1) 건수와 합계를 한 번에 조회하고, 각 건수를 합계로 나눠 비율을 계산해 돌려준다.
	답변 품질 통계라서 잡담(chat)·오류(error) 답변의 피드백은 빼고 센다 (route IN ('rag', 'no_answer'))

	순서
	1. feedback별 건수 + 합계 줄(feedback이 None인 줄)을 조회한다
	   예) [{'feedback': -1, 'count': 1}, {'feedback': 1, 'count': 3}, {'feedback': None, 'count': 4}]
	2. 줄마다 feedback 값을 보고 👍 건수·👎 건수·합계에 나눠 담는다
	   (피드백이 없는 답변은 WHERE에서 빠졌으므로 feedback이 None인 줄은 합계 줄뿐)
	3. 합계가 0보다 크면 각 건수를 합계로 나눠 비율을 계산한다 (소수점 넷째 자리까지)

	입력: 없음
	출력: dict
	      예) {'up_count': 3, 'down_count': 1, 'total': 4, 'up_ratio': 0.75, 'down_ratio': 0.25}
	      (피드백이 하나도 없으면 건수는 0, 비율은 0.0)
	"""
	sql = """
		SELECT feedback, COUNT(*) AS count
		FROM qa_logs
		WHERE feedback IS NOT NULL
			AND route IN ('rag', 'no_answer')
		GROUP BY feedback WITH ROLLUP
	"""

	with get_connection() as conn:
		cursor = conn.cursor()
		cursor.execute(sql)
		rows = cursor.fetchall()

	# 2) 👍 건수·👎 건수·합계 나눠 담기
	up_count = 0
	down_count = 0
	total = 0
	for row in rows:
		if row["feedback"] == 1:
			up_count = row["count"]
		elif row["feedback"] == -1:
			down_count = row["count"]
		else:
			total = row["count"]

	# 3) 비율 계산 (피드백이 없으면 0.0)
	up_ratio = 0.0
	down_ratio = 0.0
	if total > 0:
		up_ratio = round(up_count / total, 4)
		down_ratio = round(down_count / total, 4)

	return {
		"up_count": up_count,
		"down_count": down_count,
		"total": total,
		"up_ratio": up_ratio,
		"down_ratio": down_ratio
	}


def select_no_source_questions() -> list[dict]:
	"""
	근거 없는 질문 목록 조회 (보고서 보강 후보)

	서브쿼리로 '자료에 없습니다'(route = 'no_answer') 응답이 2회 이상 나온 질문만 골라 조회한다.

	순서
	1. 안쪽 쿼리(서브쿼리): no_answer 답변만 골라 질문별로 몇 번 나왔는지 센다
	2. 바깥 쿼리: 그중 2회 이상 나온 질문만 남긴다
	3. 많이 나온 질문부터 정렬한다

	입력: 없음
	출력: list[dict] (예: [{'question': '반도체 수출 전망은?', 'count': 3}, ...], 없으면 [])
	"""
	sql = """
		SELECT question, count
		FROM (
			SELECT question, COUNT(*) AS count
			FROM qa_logs
			WHERE route = 'no_answer'
			GROUP BY question
		) AS no_source
		WHERE count >= 2
		ORDER BY count DESC
	"""

	with get_connection() as conn:
		cursor = conn.cursor()
		cursor.execute(sql)
		rows = cursor.fetchall()

	return list(rows)


def select_route_ratio() -> list[dict]:
	"""
	처리 방식(route)별 질문 수 조회 (합계 행 포함)

	qa_logs.route별 COUNT를 GROUP BY ... WITH ROLLUP으로 집계해 합계 행까지 한 번에 조회한다.
	- route: rag(보고서 근거로 답변), no_answer('자료에 없습니다'), chat(잡담), error(오류)
	- 합계 줄은 route가 None이다 (select_persona_ratio()와 같은 방식)
	  예) [{'route': 'rag', 'count': 20}, {'route': 'no_answer', 'count': 12}, {'route': 'chat', 'count': 2}, {'route': 'error', 'count': 7}, {'route': None, 'count': 41}]
	- 비율(각 route 수 / 합계)은 get_dashboard_data_v2()가 이 결과로 계산한다

	입력: 없음
	출력: list[dict] (route별 줄 + 맨 아래 합계 줄, 기록이 없으면 [])
	"""
	sql = """
		SELECT route, COUNT(*) AS count
		FROM qa_logs
		GROUP BY route WITH ROLLUP
	"""

	with get_connection() as conn:
		cursor = conn.cursor()
		cursor.execute(sql)
		rows = cursor.fetchall()

	return list(rows)