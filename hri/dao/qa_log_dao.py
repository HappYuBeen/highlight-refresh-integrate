"""질문 기록 · insert_qa_log, select_logs (임세준) / update_feedback (류현수)

질의응답 한 번의 기록(질문·답변·응답 시간·토큰·비용 등)을 qa_logs 테이블에 저장하고 조회하는 DAO.
- insert_qa_log(): 기록 저장 (save_qa_log()가 개인정보를 가린 뒤 부름)
- select_logs(): 기록 조회 (내 기록 화면·세션 복원에서 부름)
- update_feedback(): 👍/👎 피드백 저장 (질의응답 탭 on_feedback()이 부름)
"""
from typing import Optional

from hri.dao.db import get_connection
from hri.domain.schemas import QALog


def insert_qa_log(log: QALog) -> int:
	"""
	질문 기록 한 줄 저장하기

	QALog에 담긴 값을 qa_logs 테이블에 INSERT 한다.
	- log_id(번호)와 created_at(저장 시간)은 DB가 알아서 만든다
	- feedback은 저장할 때는 비어 있고(NULL), 👍/👎를 누르면 update_feedback()이 채운다
	- qa_logs.session_id는 chat_sessions(session_id)를 FK로 참조하므로,
	  그 대화(세션) 줄이 chat_sessions에 없으면 먼저 만든다 (없으면 FK 위반으로 저장 실패)

	순서
	1. session_sql: chat_sessions에 세션 줄을 만드는 SQL을 만든다
	   (같은 대화의 두 번째 질문부터는 이미 줄이 있으므로 ON DUPLICATE KEY UPDATE로 아무것도 바꾸지 않고 넘어감)
	2. sql: 저장할 SQL을 만든다 (값 자리는 %s로 비워 둠)
	3. values: %s 자리에 순서대로 들어갈 값을 튜플로 만든다
	4. get_connection()으로 DB에 연결해서 세션 줄 → 질문 기록 순서로 실행하고 commit으로 한 번에 저장한다
	5. cursor.lastrowid로 방금 저장한 줄의 번호(log_id)를 받아서 돌려준다

	입력: log (QALog)
	출력: int (방금 저장한 기록 번호 log_id)
	"""
	# 1) 대화(세션) 줄이 없으면 만들기 (이미 있으면 그대로 둠)
	session_sql = """
		INSERT INTO chat_sessions (session_id, user_id)
		VALUES (%s, %s)
		ON DUPLICATE KEY UPDATE session_id = session_id
	"""

	# 2) 질문 기록 저장 SQL
	sql = """
		INSERT INTO qa_logs (
			user_id, session_id, question, answer, persona, route,
			has_source, response_ms, tokens, cost_krw, error
		)
		VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
	"""

	# 3) %s 자리에 들어갈 값
	values = (
		log.user_id,
		log.session_id,
		log.question,
		log.answer,
		log.persona,
		log.route,
		log.has_source,
		log.response_ms,
		log.tokens,
		log.cost_krw,
		log.error
	)

	# 4) 세션 줄 → 질문 기록 순서로 실행하고 한 번에 저장
	with get_connection() as conn:
		cursor = conn.cursor()
		cursor.execute(session_sql, (log.session_id, log.user_id))
		cursor.execute(sql, values)
		conn.commit()

		# 5) 마지막에 실행한 qa_logs INSERT의 번호
		log_id = cursor.lastrowid

	return log_id


def select_logs(user_id: str, session_id: Optional[str] = None) -> list[QALog]:
	"""
	질문 기록 조회하기 (최신순)

	user_id(필수)로 그 사람의 기록을 찾고, session_id를 주면 그 대화의 기록만 찾는다.
	- session_id가 None이면: 그 사람의 모든 대화 기록 (내 기록 화면의 목록용)
	- session_id가 있으면: 그 대화 하나의 기록만 (세션 복원·대화 이어가기용)

	순서
	1. sql: 기본 SELECT 문을 만든다 (WHERE user_id = %s)
	2. session_id가 있으면 "AND session_id = %s" 조건과 값을 하나 더 붙인다
	3. 맨 뒤에 ORDER BY created_at DESC를 붙여 최신 기록부터 나오게 한다
	   (같은 초에 저장된 기록은 log_id가 큰 것(나중에 저장한 것)이 먼저 나오게 log_id DESC도 붙임)
	4. get_connection()으로 실행하고 fetchall()로 모든 줄을 받는다 (줄마다 딕셔너리)
	5. 딕셔너리 한 줄씩 QALog로 바꿔 리스트에 담아서 돌려준다

	입력: user_id (아이디, 필수), session_id (대화 묶음 번호, 없으면 None)
	출력: list[QALog] (최신순, 기록이 없으면 빈 리스트 [])
	"""
	# 1) 기본 SQL (이 사람의 기록만)
	sql = """
		SELECT log_id, user_id, session_id, question, answer, persona, route,
			has_source, response_ms, tokens, cost_krw, feedback, error, created_at
		FROM qa_logs
		WHERE user_id = %s
	"""
	values = [user_id]

	# 2) session_id가 있으면 그 대화만
	if session_id != None:
		sql = sql + " AND session_id = %s"
		values.append(session_id)

	# 3) 최신순 정렬
	sql = sql + " ORDER BY created_at DESC, log_id DESC"

	# 4) 실행하고 모든 줄 받기
	with get_connection() as conn:
		cursor = conn.cursor()
		cursor.execute(sql, values)
		rows = cursor.fetchall()

	# 5) 한 줄씩 QALog로 바꾸기
	logs = []
	for row in rows:
		log = QALog(
			log_id=row["log_id"],
			user_id=row["user_id"],
			session_id=row["session_id"],
			question=row["question"],
			answer=row["answer"],
			persona=row["persona"],
			route=row["route"],
			has_source=row["has_source"],
			response_ms=row["response_ms"],
			tokens=row["tokens"],
			cost_krw=row["cost_krw"],
			feedback=row["feedback"],
			error=row["error"],
			created_at=row["created_at"]
		)
		logs.append(log)

	return logs


def update_feedback(log_id: int, feedback: int) -> bool:
	"""
	질문 기록에 👍/👎 피드백 저장하기 (류현수)

	qa_logs의 log_id 줄에 feedback 값을 넣는다. 이미 값이 있으면 새 값으로 바꾼다 (👍 → 👎로 바꾸기 가능).
	- feedback은 1(👍) 또는 -1(👎)만 받는다 (qa_logs CHECK 제약과 같음). 그 밖의 값은 ValueError

	입력: log_id (질문 기록 번호), feedback (1 또는 -1)
	출력: bool (그 번호의 기록이 있어서 저장했으면 True, 없으면 False)
	"""
	if feedback not in (1, -1):
		raise ValueError(f"feedback은 1 또는 -1이어야 합니다: {feedback}")

	sql = "UPDATE qa_logs SET feedback = %s WHERE log_id = %s"

	with get_connection() as conn:
		cursor = conn.cursor()
		cursor.execute(sql, (feedback, log_id))
		conn.commit()
		# 같은 값으로 다시 저장하면 MySQL은 바뀐 줄 수를 0으로 알려 주므로, 기록이 있는지는 따로 확인한다
		if cursor.rowcount == 0:
			cursor.execute("SELECT 1 FROM qa_logs WHERE log_id = %s", (log_id,))
			return cursor.fetchone() is not None

	return True
