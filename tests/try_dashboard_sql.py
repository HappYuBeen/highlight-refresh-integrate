"""대시보드 1차 SQL 테스트 (임세준) - 커밋하지 않는 파일

MySQL 대신 파이썬 기본 SQLite(메모리 DB)에 qa_logs·llm_usage를 잠깐 만들어서
진짜 dashboard_dao 함수 4개의 SQL을 그대로 실행해 본다. (DB 설치·.env 필요 없음)

실행 (레포 맨 위 폴더에서):
	poetry run python -m tests.try_dashboard_sql

※ SQLite와 MySQL의 차이 (테스트 결과 볼 때 참고)
	- 날짜(day)가 date가 아니라 글자('2026-10-28')로 나옴
	- 같은 날 flow 순서가 알파벳순(ingest → qa → summary)으로 나옴 (MySQL은 ENUM 순서 qa → ingest → summary)
	- 비율이 소수 넷째 자리로 잘리지 않음
"""
import sqlite3

from hri.dao import dashboard_dao


# ----------------------------------------------------------------
# 1. 메모리 DB 만들기
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
		route TEXT, has_source INTEGER, response_ms INTEGER, tokens INTEGER, cost_krw REAL,
		feedback INTEGER, error TEXT, created_at TEXT
	);
	CREATE TABLE llm_usage (
		usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
		flow TEXT, user_id TEXT, ref_id INTEGER, model TEXT,
		input_tokens INTEGER, output_tokens INTEGER, created_at TEXT
	);
""")


class FakeConnection:
	"""with get_connection() as conn: 모양 그대로 쓸 수 있게 감싼 것"""

	def __enter__(self):
		return db

	def __exit__(self, *args):
		return False


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


def add_log(question, route, response_ms, error=None, day="2026-10-28", seconds=0):
	db.execute(
		"INSERT INTO qa_logs (user_id, session_id, question, answer, persona, route, has_source, response_ms, tokens, cost_krw, error, created_at) "
		"VALUES ('kim', 's-1', ?, 'a', '실무자', ?, 1, ?, 10, 0.1, ?, ?)",
		(question, route, response_ms, error, f"{day} 10:00:{seconds:02d}")
	)


def add_usage(flow, model, input_tokens, output_tokens, day="2026-10-28"):
	db.execute(
		"INSERT INTO llm_usage (flow, user_id, model, input_tokens, output_tokens, created_at) VALUES (?, 'kim', ?, ?, ?, ?)",
		(flow, model, input_tokens, output_tokens, f"{day} 10:00:00")
	)


# ----------------------------------------------------------------
# 3. 빈 DB일 때
# ----------------------------------------------------------------
check("1 빈 DB: select_top_questions → []", dashboard_dao.select_top_questions() == [])
check("2 빈 DB: select_source_ratio → 0.0", dashboard_dao.select_source_ratio() == 0.0)
check("3 빈 DB: select_perf_cost → []", dashboard_dao.select_perf_cost() == [])
check("4 빈 DB: select_recent_errors → []", dashboard_dao.select_recent_errors() == [])

# ----------------------------------------------------------------
# 4. 데이터 넣기
# ----------------------------------------------------------------
for i in range(5):
	add_log("기준금리 전망은?", "rag", 1000)
for i in range(3):
	add_log("환율은?", "rag", 2000)
add_log("없는 자료 질문", "no_answer", 3000)
add_log("안녕", "chat", 100)
add_log("", "error", 0, error="빈 질문", seconds=30)
add_log("그래프 질문", "rag", 500, error="LLM 시간 초과", seconds=20)
for i in range(12):
	add_log(f"기타 질문 {i}", "rag", 1000, day="2026-10-27")
for i in range(25):
	add_log(f"에러 {i}", "error", 0, error=f"err{i}", day="2026-10-26", seconds=i)

add_usage("qa", "gpt-5-mini-2025-08-07", 1000, 200)
add_usage("qa", "gpt-5-mini-2025-08-07", 200, 100)
add_usage("ingest", "gpt-5.5-2026-04-23", 100000, 20000)
add_usage("summary", "gpt-5-mini-2025-08-07", 800, 300, day="2026-10-27")

# ----------------------------------------------------------------
# 5. 함수별 확인
# ----------------------------------------------------------------
top = dashboard_dao.select_top_questions()
check("5 TOP 10: 10개", len(top) == 10, len(top))
check("6 TOP 10: 1등 기준금리 5회, 2등 환율 3회", top[0] == {"question": "기준금리 전망은?", "count": 5} and top[1] == {"question": "환율은?", "count": 3}, top[:2])

ratio = dashboard_dao.select_source_ratio()
check("7 비율: no_answer 1 / (rag 21 + no_answer 1) = 0.0455 (chat·error 제외)", round(ratio, 4) == 0.0455, round(ratio, 4))

perf = dashboard_dao.select_perf_cost()
print("     select_perf_cost 결과:")
for row in perf:
	print("      ", row)
check("8 날짜·flow·모델별 3줄, cost_krw 없음", len(perf) == 3 and "cost_krw" not in perf[0])
qa_row = None
for row in perf:
	if row["flow"] == "qa":
		qa_row = row
check("9 qa 토큰 합계 1200 / 300", qa_row["input_tokens"] == 1200 and qa_row["output_tokens"] == 300, qa_row)
check("10 그날 평균 응답 시간이 붙음", qa_row["avg_response_ms"] != None, qa_row["avg_response_ms"])

errors = dashboard_dao.select_recent_errors()
check("11 최근 에러: 20건만", len(errors) == 20, len(errors))
check("12 최근 에러: 최신 2건이 '빈 질문' → 'LLM 시간 초과'", errors[0]["error"] == "빈 질문" and errors[1]["error"] == "LLM 시간 초과", (errors[0]["error"], errors[1]["error"]))
all_ok = True
for e in errors:
	if e["route"] != "error" and e["error"] == None:
		all_ok = False
check("13 최근 에러: route='error'이거나 error 있는 것만 (잡담 제외)", all_ok)

print(f"\n통과 {passed}/{total}")