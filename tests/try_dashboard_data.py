"""대시보드 1차 데이터 테스트 (임세준) - 커밋하지 않는 파일

진짜 get_dashboard_data()와 진짜 dashboard_dao SQL·table_to_markdown()·cost_korean_won()을 그대로 쓰고,
DB만 파이썬 기본 SQLite(메모리 DB)로, 보고서 목록(search_reports)과 환율(get_usd_krw)만 가짜로 바꾼다.
(MySQL 설치 필요 없음. cost_korean_won()이 단가를 읽으므로 .env는 있어야 함)

실행 (레포 맨 위 폴더에서):
	poetry run python -m tests.try_dashboard_data
"""
import sqlite3
from datetime import date

from hri.dao import dashboard_dao
from hri.domain.schemas import DashboardData, Report
from hri.llm.llm_client import cost_korean_won
from hri.view import admin_view


# ----------------------------------------------------------------
# 1. 메모리 DB (try_dashboard_sql.py와 같은 방식)
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
# 2. 가짜 보고서 목록·환율
# ----------------------------------------------------------------
FAKE_REPORTS = []
admin_view.search_reports = lambda keyword="", limit=None: FAKE_REPORTS
admin_view.get_usd_krw = lambda: 1400.0

# ----------------------------------------------------------------
# 3. 테스트 함수
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
# 4. 빈 DB일 때
# ----------------------------------------------------------------
empty = admin_view.get_dashboard_data()
check("1 빈 DB: DashboardData로 돌려줌", isinstance(empty, DashboardData))
check("2 빈 DB: 숫자는 0.0", empty.source_ratio == 0.0 and empty.avg_response_ms == 0.0 and empty.cost_krw == 0.0)
check("3 빈 DB: 표는 머리글만", empty.top_questions == "| 질문 | 횟수 |\n|---|---|" and empty.recent_errors.count("\n") == 1, repr(empty.top_questions))

# ----------------------------------------------------------------
# 5. 데이터 넣기
# ----------------------------------------------------------------
for i in range(5):
	add_log("기준금리 전망은?", "rag", 1000)
for i in range(3):
	add_log("환율은?", "rag", 2000)
add_log("없는 자료 질문", "no_answer", 3000)
add_log("", "error", 0, error="빈 질문", seconds=30)
add_log("그래프 질문", "rag", 500, error="LLM 시간 초과", seconds=20)
for i in range(12):
	add_log(f"기타 질문 {i}", "rag", 1000, day="2026-10-27")

add_usage("qa", "gpt-5-mini-2025-08-07", 1000, 200)
add_usage("qa", "gpt-5-mini-2025-08-07", 200, 100)
add_usage("ingest", "gpt-5.5-2026-04-23", 100000, 20000)
add_usage("summary", "gpt-5-mini-2025-08-07", 800, 300, day="2026-10-27")
add_usage("summary", "gpt-5-mini-2025-08-07", 200, 100)

FAKE_REPORTS.append(Report(report_id=2, title="2027 경제 전망", publisher="HRI", published_date=date(2026, 10, 1), chunk_count=42, file_path="data/raw/a.pdf", uploaded_by="admin"))
FAKE_REPORTS.append(Report(report_id=1, title="발행일 없는 보고서", file_path="data/raw/b.pdf", uploaded_by="admin"))

# ----------------------------------------------------------------
# 6. 결과 확인
# ----------------------------------------------------------------
data = admin_view.get_dashboard_data()
print("\n----- 화면에 보일 값 -----")
for name, value in data.model_dump().items():
	print(f"[{name}]\n{value}\n")
print("--------------------------")

check("4 TOP 10: 1등 '기준금리 전망은?' 5회", "| 기준금리 전망은? | 5 |" in data.top_questions)
check("5 자료 유무 비율: no_answer 1 / (rag 21 + no_answer 1)", round(data.source_ratio, 4) == 0.0455, round(data.source_ratio, 4))

# 평균 응답 시간: 토큰 기록이 있는 날(10-28, 10-27)의 일자별 평균을 날짜마다 한 번씩만 평균
day_avg = {}
for row in dashboard_dao.select_perf_cost():
	day_avg[row["day"]] = row["avg_response_ms"]
expected_ms = round((day_avg["2026-10-28"] + day_avg["2026-10-27"]) / 2, 1)
check("6 평균 응답 시간: 날짜별 한 번씩 평균", data.avg_response_ms == expected_ms, f"{data.avg_response_ms} (10-28 {day_avg['2026-10-28']}, 10-27 {day_avg['2026-10-27']})")

check("7 flow별 토큰: qa 1200 / 300", "| qa | 1200 | 300 |" in data.usage_by_flow)
check("8 flow별 토큰: summary는 이틀 치를 더해 1000 / 400", "| summary | 1000 | 400 |" in data.usage_by_flow)
check("9 flow별 토큰: flow마다 한 줄 (머리글·구분선 + 3줄)", data.usage_by_flow.count("\n") == 4)

qa_cost = cost_korean_won({"gpt-5-mini-2025-08-07": {"input_tokens": 1200, "output_tokens": 300}}, 1400.0)
ingest_cost = cost_korean_won({"gpt-5.5-2026-04-23": {"input_tokens": 100000, "output_tokens": 20000}}, 1400.0)
summary_cost = cost_korean_won({"gpt-5-mini-2025-08-07": {"input_tokens": 1000, "output_tokens": 400}}, 1400.0)
check("10 원화 비용 합계 = flow별 cost_korean_won() 합", data.cost_krw == round(qa_cost + ingest_cost + summary_cost, 4), data.cost_krw)
check("11 flow 표에 원화 비용 칸", f"{round(qa_cost, 2)}원" in data.usage_by_flow, f"qa {round(qa_cost, 2)}원")

check("12 보고서 목록: [제목, 발행일, 청크 수]", "| 2027 경제 전망 | 2026-10-01 | 42 |" in data.reports)
check("13 보고서 목록: 발행일 없으면 빈칸", "| 발행일 없는 보고서 |  | 0 |" in data.reports)
check("14 최근 에러: 최신 '빈 질문'이 첫 줄", data.recent_errors.split("\n")[2].endswith("| 빈 질문 |"), data.recent_errors.split("\n")[2])

print(f"\n통과 {passed}/{total}")