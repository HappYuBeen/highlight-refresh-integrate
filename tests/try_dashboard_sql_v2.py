"""대시보드 2차 SQL 테스트 (임세준) - 커밋하지 않는 파일

MySQL 대신 파이썬 기본 SQLite(메모리 DB)에 qa_logs와 VIEW v_daily_usage를 잠깐 만들어서
진짜 dashboard_dao 2차 함수 4개의 SQL을 그대로 실행해 본다. (DB 설치·.env 필요 없음)

실행 (레포 맨 위 폴더에서):
	poetry run python -m tests.try_dashboard_sql_v2

※ SQLite에는 WITH ROLLUP이 없어서, 이 테스트에서만 FakeCursor가 MySQL처럼 흉내 낸다
	- 'WITH ROLLUP'을 떼고 실행한 뒤, 맨 아래에 합계 줄(묶은 칸 None, count는 전체 합)을 붙인다
	- 묶을 줄이 하나도 없으면 MySQL처럼 합계 줄도 붙이지 않는다
※ SQLite와 MySQL의 차이: 날짜(day)가 date가 아니라 글자('2026-10-27')로 나온다
"""
import sqlite3

from hri.dao import dashboard_dao


# ----------------------------------------------------------------
# 1. 메모리 DB 만들기 (VIEW는 sql/schema.sql과 같게)
# ----------------------------------------------------------------
def dict_row(cursor, row):
	"""pymysql DictCursor처럼 줄마다 딕셔너리로 돌려주기"""
	result = {}
	for i in range(len(cursor.description)):
		result[cursor.description[i][0]] = row[i]
	return result


db = sqlite3.connect(":memory:")
db.row_factory = dict_row

db.executescript("""
	CREATE TABLE qa_logs (
		log_id INTEGER PRIMARY KEY AUTOINCREMENT,
		user_id TEXT, session_id TEXT, question TEXT, answer TEXT, persona TEXT,
		route TEXT, response_ms INTEGER, feedback INTEGER, created_at TEXT
	);
	CREATE VIEW v_daily_usage AS
	SELECT
		DATE(created_at) AS day,
		COUNT(*) AS question_count,
		AVG(response_ms) AS avg_response_ms
	FROM qa_logs
	GROUP BY DATE(created_at);
""")


class FakeCursor:
	"""SQLite로 실행하되 WITH ROLLUP만 MySQL처럼 흉내 내는 커서"""

	def __init__(self):
		self.rows = []

	def execute(self, sql, values=()):
		# WITH ROLLUP이 없으면 그대로 실행
		if "WITH ROLLUP" not in sql:
			self.rows = db.execute(sql, values).fetchall()
			return

		# WITH ROLLUP을 떼고 실행한 뒤, 맨 아래에 합계 줄 붙이기
		rows = db.execute(sql.replace("WITH ROLLUP", ""), values).fetchall()
		if len(rows) > 0:
			total = 0
			for row in rows:
				total = total + row["count"]
			total_row = {}
			for key in rows[0]:
				total_row[key] = None
			total_row["count"] = total
			rows.append(total_row)
		self.rows = rows

	def fetchall(self):
		return self.rows


class FakeConnection:
	"""with get_connection() as conn: 모양 그대로 쓸 수 있게 감싼 것"""

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return False

	def cursor(self):
		return FakeCursor()


dashboard_dao.get_connection = lambda: FakeConnection()


# ----------------------------------------------------------------
# 2. 테스트 함수
# ----------------------------------------------------------------
passed = 0
total = 0


def check(name, ok, info=""):
	global passed, total
	total = total + 1
	if ok:
		passed = passed + 1
		print(f"✅ {name} {info}")
	else:
		print(f"❌ {name} {info}")


def add_log(question, persona, route, response_ms, feedback, created_at):
	db.execute(
		"INSERT INTO qa_logs (user_id, session_id, question, answer, persona, route, response_ms, feedback, created_at) "
		"VALUES ('kim', 's-1', ?, 'a', ?, ?, ?, ?, ?)",
		(question, persona, route, response_ms, feedback, created_at)
	)


# ----------------------------------------------------------------
# 3. 빈 DB일 때
# ----------------------------------------------------------------
check("1 빈 DB: select_daily_counts → []", dashboard_dao.select_daily_counts() == [])
check("2 빈 DB: select_persona_ratio → [] (합계 줄도 없음)", dashboard_dao.select_persona_ratio() == [])
empty_feedback = dashboard_dao.select_feedback_ratio()
check("3 빈 DB: select_feedback_ratio → 건수 0, 비율 0.0 (0으로 나누기 에러 없음)", empty_feedback == {"up_count": 0, "down_count": 0, "total": 0, "up_ratio": 0.0, "down_ratio": 0.0}, empty_feedback)
check("4 빈 DB: select_no_source_questions → []", dashboard_dao.select_no_source_questions() == [])

# ----------------------------------------------------------------
# 4. 데이터 넣기 (날짜 3일, 입문자 3·실무자 6, 👍 3·👎 1, no_answer 반도체 3·부동산 2·한 번만 1)
# ----------------------------------------------------------------
add_log("기준금리?", "실무자", "rag", 1000, 1, "2026-10-27 10:00:00")
add_log("기준금리?", "실무자", "rag", 2000, 1, "2026-10-27 11:00:00")
add_log("반도체 수출?", "입문자", "no_answer", 3000, -1, "2026-10-28 10:00:00")
add_log("반도체 수출?", "입문자", "no_answer", 1000, None, "2026-10-28 10:01:00")
add_log("반도체 수출?", "실무자", "no_answer", 500, None, "2026-10-28 10:02:00")
add_log("부동산?", "실무자", "no_answer", 700, None, "2026-10-28 10:03:00")
add_log("부동산?", "실무자", "no_answer", 900, None, "2026-10-29 10:03:00")
add_log("한 번만?", "실무자", "no_answer", 900, None, "2026-10-29 10:04:00")
add_log("안녕", "입문자", "chat", None, 1, "2026-10-29 10:05:00")

# ----------------------------------------------------------------
# 5. 함수별 확인
# ----------------------------------------------------------------
daily = dashboard_dao.select_daily_counts()
print("     select_daily_counts 결과:")
for row in daily:
	print("      ", row)
check("5 사용 추이: 날짜 3줄, 오래된 날짜부터", len(daily) == 3 and daily[0]["day"] == "2026-10-27" and daily[2]["day"] == "2026-10-29")
check("6 사용 추이: 10-27 질문 2건, 평균 1500.0", daily[0]["question_count"] == 2 and daily[0]["avg_response_ms"] == 1500.0, daily[0])
check("7 사용 추이: 10-29 질문 3건(응답 시간 없는 잡담도 셈), 평균 900.0(빈 값은 빼고 평균)", daily[2]["question_count"] == 3 and daily[2]["avg_response_ms"] == 900.0, daily[2])

persona = dashboard_dao.select_persona_ratio()
print("     select_persona_ratio 결과:", persona)
counts = {}
for row in persona:
	counts[row["persona"]] = row["count"]
check("8 페르소나: 실무자 6, 입문자 3", counts.get("실무자") == 6 and counts.get("입문자") == 3)
check("9 페르소나: 맨 아래 합계 줄 (persona=None, 9)", persona[-1] == {"persona": None, "count": 9}, persona[-1])

feedback = dashboard_dao.select_feedback_ratio()
print("     select_feedback_ratio 결과:", feedback)
check("10 피드백: 👍 3, 👎 1, 합계 4 (피드백 없는 답변은 빠짐)", feedback["up_count"] == 3 and feedback["down_count"] == 1 and feedback["total"] == 4)
check("11 피드백: 비율 0.75 / 0.25", feedback["up_ratio"] == 0.75 and feedback["down_ratio"] == 0.25)

no_source = dashboard_dao.select_no_source_questions()
print("     select_no_source_questions 결과:", no_source)
check("12 근거 없는 질문: 2회 이상만 (반도체 3, 부동산 2)", no_source == [{"question": "반도체 수출?", "count": 3}, {"question": "부동산?", "count": 2}])
check("13 근거 없는 질문: 1번만 나온 질문은 빠짐", "한 번만?" not in str(no_source))

print(f"\n통과 {passed}/{total}")