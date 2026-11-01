"""관리자 대시보드 처리 방식(route)별 비율 테스트 (임세준) - 커밋하지 않는 파일

1부-1: MySQL 대신 파이썬 기본 SQLite(메모리 DB)에 qa_logs를 만들어 진짜 select_route_ratio()의 SQL을 그대로 실행
1부-2: 진짜 get_dashboard_data_v2()·build_admin_tab()·on_refresh()를 쓰고, DB가 필요한 함수만 가짜로 바꿔 확인
2부: 화면을 띄워 관리자 로그인 → 사용 현황에 처리 방식 표가 바로 보이는지 직접 보기
(DB 설치·.env 필요 없음, 데이터는 실제 DB 결과 rag 20 · no_answer 12 · chat 2 · error 7과 같게 넣음)

실행 (레포 맨 위 폴더에서):
	poetry run python -m tests.try_route_ratio
	→ 브라우저에서 http://127.0.0.1:7860 (끝내려면 터미널에서 Ctrl+C)

※ SQLite에는 WITH ROLLUP이 없어서, 이 테스트에서만 FakeCursor가 MySQL처럼 흉내 낸다
	- 'WITH ROLLUP'을 떼고 실행한 뒤, 맨 아래에 합계 줄(묶은 칸 None, count는 전체 합)을 붙인다
	- 묶을 줄이 하나도 없으면 MySQL처럼 합계 줄도 붙이지 않는다
"""
import sqlite3

import gradio as gr

from hri.dao import dashboard_dao
from hri.domain.schemas import DashboardData
from hri.view import admin_view
from hri.view.admin_view import build_admin_tab, on_refresh


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
db.execute("CREATE TABLE qa_logs (log_id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT, route TEXT)")


class FakeCursor:
	"""SQLite로 실행하되 WITH ROLLUP만 MySQL처럼 흉내 내는 커서"""

	def __init__(self):
		self.rows = []

	def execute(self, sql, values=()):
		if "WITH ROLLUP" not in sql:
			self.rows = db.execute(sql, values).fetchall()
			return

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


# ----------------------------------------------------------------
# 3. 1부-1: select_route_ratio() SQL
# ----------------------------------------------------------------
check("1 빈 DB: select_route_ratio → [] (합계 줄도 없음)", dashboard_dao.select_route_ratio() == [])

for route, count in [("rag", 20), ("no_answer", 12), ("chat", 2), ("error", 7)]:
	for i in range(count):
		db.execute("INSERT INTO qa_logs (question, route) VALUES (?, ?)", (f"질문 {i}", route))

rows = dashboard_dao.select_route_ratio()
print("     select_route_ratio 결과:", rows)
counts = {}
for row in rows:
	counts[row["route"]] = row["count"]
check("2 route별 질문 수: rag 20, no_answer 12, chat 2, error 7", counts.get("rag") == 20 and counts.get("no_answer") == 12 and counts.get("chat") == 2 and counts.get("error") == 7)
check("3 맨 아래 합계 줄 (route=None, 41)", rows[-1] == {"route": None, "count": 41}, rows[-1])

# ----------------------------------------------------------------
# 4. 1부-2: get_dashboard_data_v2()·on_refresh()·build_admin_tab()
# ----------------------------------------------------------------
FAKE_V1 = DashboardData(
	top_questions="| 질문 | 횟수 |\n|---|---|\n| 반도체 수출? | 3 |",
	source_ratio=0.375,
	avg_response_ms=1233.3,
	usage_by_flow="| flow | 입력 토큰 | 출력 토큰 | 원화 비용 |\n|---|---|---|---|",
	cost_krw=0.0,
	reports="| 제목 | 발행일 | 청크 수 |\n|---|---|---|",
	recent_errors="| 시각 | 사용자 | 질문 | 에러 |\n|---|---|---|---|"
)
EMPTY_FEEDBACK = {"up_count": 0, "down_count": 0, "total": 0, "up_ratio": 0.0, "down_ratio": 0.0}


def use_fake(route_rows):
	"""admin_view가 부르는 DB 함수들을 가짜로 바꾸기 (처리 방식만 route_rows로)"""
	admin_view.get_dashboard_data = lambda: FAKE_V1.model_copy()
	admin_view.select_daily_counts = lambda: []
	admin_view.select_persona_ratio = lambda: []
	admin_view.select_feedback_ratio = lambda: EMPTY_FEEDBACK
	admin_view.select_no_source_questions = lambda: []
	admin_view.select_route_ratio = lambda: route_rows


use_fake(rows)
data = admin_view.get_dashboard_data_v2()
print(f"     [route_ratio]\n{data.route_ratio}")
check("4 처리 방식 표: 보고서 근거 답변 20 (48.8%)", "| 보고서 근거 답변 (rag) | 20 | 48.8% |" in data.route_ratio)
check("5 처리 방식 표: 자료에 없음 12 (29.3%), 잡담 2 (4.9%), 오류 7 (17.1%)", "| 자료에 없음 (no_answer) | 12 | 29.3% |" in data.route_ratio and "| 잡담 (chat) | 2 | 4.9% |" in data.route_ratio and "| 오류 (error) | 7 | 17.1% |" in data.route_ratio)
check("6 처리 방식 표: 합계 41", "| 합계 | 41 |  |" in data.route_ratio)

use_fake([])
empty = admin_view.get_dashboard_data_v2()
check("7 기록이 없으면 처리 방식 표는 머리글만", empty.route_ratio.count("\n") == 1, empty.route_ratio)

use_fake(rows)
result = on_refresh()
check("8 on_refresh → tuple 12개, 5번째가 처리 방식 표", len(result) == 12 and result[4] == data.route_ratio, len(result))

with gr.Blocks() as demo:
	with gr.Tabs():
		admin_tab = build_admin_tab()

refresh_outputs = []
for dep in demo.fns.values():
	for target_id, name in dep.targets:
		if target_id == admin_tab._id and name == "select":
			refresh_outputs = dep.outputs
check("9 build_admin_tab: on_refresh 출력 칸 12개 (페르소나 비율 바로 다음이 처리 방식 표)", len(refresh_outputs) == 12 and isinstance(refresh_outputs[4], gr.Markdown), len(refresh_outputs))

print(f"\n통과 {passed}/{total}")


# ----------------------------------------------------------------
# 5. 2부: 화면 띄우기 (로그인 버튼 = on_login이 관리자 탭을 보이게 하고 선택하는 것 흉내)
# ----------------------------------------------------------------
def fake_login():
	"""테스트용: 관리자 탭을 보이게 하고 그 탭으로 이동 (실제 on_login이 하는 일)"""
	return gr.update(visible=True), gr.update(visible="hidden"), gr.Tabs(selected="admin")


with gr.Blocks() as screen:
	with gr.Tabs(selected="login") as tabs:
		with gr.Tab("로그인", id="login") as login_tab:
			login_btn = gr.Button("(테스트용) 관리자로 로그인 → 사용 현황에 처리 방식 표가 바로 보여야 함")
		screen_tab = build_admin_tab()

	login_btn.click(fn=fake_login, outputs=[screen_tab, login_tab, tabs])

screen.launch(theme=gr.themes.Soft())