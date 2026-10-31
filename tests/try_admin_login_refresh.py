"""관리자 로그인 시 관리자 탭 선택 + 대시보드 자동 갱신 테스트 (임세준) - 커밋하지 않는 파일

진짜 build_app()(app.py), on_login(), build_admin_tab(), on_refresh()를 그대로 쓰고,
DB·그래프·LLM이 필요한 서비스만 가짜로 바꾼다.
1부: 탭 순서·연결·on_login 결과 확인 (✅/❌) → 2부: 앱을 띄워서 admin으로 직접 로그인해 보기

실행 (레포 맨 위 폴더에서):
	poetry run python -m tests.try_admin_login_refresh
	→ 브라우저에서 http://127.0.0.1:7860 (끝내려면 Ctrl+C)
	   - 아이디 admin (비밀번호 아무거나): 관리자 탭만 보이고, 새로고침을 안 눌러도 통계가 바로 보여야 함
	   - 아이디 kim (비밀번호 아무거나): 보고서·요약·질의응답·내 기록 탭, 질의응답 탭 선택
"""
import sys
import time
import types
from datetime import date, datetime

import gradio as gr


def make_fake_module(name, **attrs):
	"""이름이 name인 가짜 모듈을 만들어 import할 때 이걸 쓰게 한다"""
	module = types.ModuleType(name)
	for key, value in attrs.items():
		setattr(module, key, value)
	sys.modules[name] = module
	return module


# ----------------------------------------------------------------
# 1. 가짜 서비스 (DB·그래프·LLM 대신)
# ----------------------------------------------------------------
class FakeReport:
	def __init__(self, report_id, title):
		self.report_id = report_id
		self.title = title
		self.publisher = "HRI"
		self.published_date = date(2026, 10, 1)
		self.page_count = 12
		self.chunk_count = 30


REPORTS = [FakeReport(1, "2027 경제 전망")]


def fake_search_reports(keyword="", limit=None, user_id=None):
	return list(REPORTS)

def fake_owner_filter(user):
	"""가짜 owner_filter: 진짜와 같게 관리자는 None(전체), 일반 사용자는 본인 아이디, 로그인 전은 ''"""
	if user is None:
		return ""
	if isinstance(user, dict):
		role = user.get("role")
		user_id = user.get("user_id")
	else:
		role = getattr(user, "role", None)
		user_id = getattr(user, "user_id", None)
	if role == "admin":
		return None
	return user_id or ""


def fake_upload_report(path, user_id):
	"""가짜 업로드: 단계 3개를 0.3초씩 보여 주고, 보고서 목록에 새 보고서를 추가한다"""
	for step in ["validate", "load_text", "persist"]:
		time.sleep(0.3)
		yield {"type": "progress", "step": step}
	REPORTS.append(FakeReport(2, "반도체 산업 동향"))
	yield {"type": "result", "report_id": 2, "counts": {"text": 20, "table": 3, "chart": 1}, "warnings": []}


class FakeUser:
	def __init__(self, user_id, role):
		self.user_id = user_id
		self.name = user_id
		self.role = role
		self.created_at = datetime(2026, 10, 1)


def fake_login(user_id, password):
	"""가짜 로그인: 아이디가 admin이면 관리자, 아니면 일반 사용자 (비밀번호는 보지 않음)"""
	if user_id == "admin":
		return FakeUser(user_id, "admin")
	return FakeUser(user_id, "user")


class FakeSummary:
	three_lines = ["성장률 1.9% 전망", "수출 회복", "물가 안정"]
	sections = []


make_fake_module("hri.graph.registry", get_ingest_graph=lambda: None, get_qa_graph=lambda: None, get_summary_graph=lambda: None, get_agent_graph=lambda: None)
make_fake_module("hri.config.settings", load_settings=lambda: None)
make_fake_module("hri.service.report_service", search_reports=fake_search_reports, owner_filter=fake_owner_filter)
make_fake_module("hri.service.document_service", upload_report=fake_upload_report)
make_fake_module("hri.service.user_service", login=fake_login, register=lambda *a: True)
make_fake_module("hri.service.summary_service", summarize_report=lambda report_id: FakeSummary())
make_fake_module("hri.router.tool_router", run_agent_with_tools=lambda request, report_ids: (f"요청 '{request}' 처리 완료 (보고서 {report_ids})", ["fetch_ecos"]))
make_fake_module("hri.service.term_service", MAX_TERM_LENGTH=20, explain_term=lambda term: None)
make_fake_module("hri.service.history_service", get_history=lambda user_id: [], restore_session=lambda session_id, user_id: [])
make_fake_module("hri.service.indicator_service", build_compare_table=lambda ids: "", plot_indicators=lambda ids: None, PLOT_DIR=None, _use_korean_font=lambda: None)
make_fake_module("hri.llm.llm_client", cost_korean_won=lambda usage, rate: 0.0, get_usd_krw=lambda: 1400.0)
make_fake_module("hri.dao.qa_log_dao", update_feedback=lambda log_id, feedback: True)
make_fake_module("hri.dao.dashboard_dao", **{name: (lambda: []) for name in [
	"select_daily_counts", "select_feedback_ratio", "select_no_source_questions", "select_perf_cost",
	"select_persona_ratio", "select_recent_errors", "select_source_ratio", "select_top_questions"]})

import hri.domain.schemas as schemas  # noqa: E402
if not hasattr(schemas, "AnswerResult"):
	schemas.AnswerResult = type("AnswerResult", (), {})

from hri.domain.schemas import DashboardData  # noqa: E402
from hri.view import admin_view, app  # noqa: E402
from hri.view.user_view import on_login  # noqa: E402

# 가짜 대시보드 데이터 (부를 때마다 횟수를 셈)
calls = {"count": 0}


def fake_dashboard():
	"""부른 횟수를 세고 가짜 대시보드 데이터를 돌려준다 (차트는 없음)"""
	calls["count"] = calls["count"] + 1
	return DashboardData(
		top_questions="| 질문 | 횟수 |\n|---|---|\n| 반도체 수출? | 3 |",
		source_ratio=0.75,
		avg_response_ms=1233.3,
		usage_by_flow="| flow | 입력 토큰 | 출력 토큰 | 원화 비용 |\n|---|---|---|---|\n| qa | 1200 | 300 | 1.26원 |",
		cost_krw=1.26,
		reports="| 제목 | 발행일 | 청크 수 |\n|---|---|---|\n| 2027 경제 전망 | 2026-10-01 | 42 |",
		recent_errors="| 시각 | 사용자 | 질문 | 에러 |\n|---|---|---|---|",
		daily_chart=None,
		persona_ratio="| 페르소나 | 질문 수 | 비율 |\n|---|---|---|\n| 실무자 | 6 | 100.0% |",
		feedback_ratio="| 구분 | 건수 | 비율 |\n|---|---|---|",
		no_source_questions="| 질문 | '자료에 없습니다' 횟수 |\n|---|---|"
	)


admin_view.get_dashboard_data_v2 = fake_dashboard

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
# 3. 1부: 탭 순서·연결·on_login 결과 확인
# ----------------------------------------------------------------
demo = app.build_app()

# 맨 바깥 탭 묶음(로그인 탭이 들어 있는 gr.Tabs) 찾기
main_tabs = None
for block in demo.blocks.values():
	if isinstance(block, gr.Tabs):
		for child in block.children:
			if isinstance(child, gr.Tab) and child.id == "login":
				main_tabs = block

tab_ids = []
admin_tab = None
for child in main_tabs.children:
	if isinstance(child, gr.Tab):
		tab_ids.append(child.id)
		if child.id == "admin":
			admin_tab = child
print("     탭 순서:", tab_ids)

check("1 탭 6개 (로그인·관리자·보고서·요약·질의응답·내 기록)", len(tab_ids) == 6 and "admin" in tab_ids, len(tab_ids))
check("2 관리자 탭은 로그인 탭 바로 뒤 (숨겨진 사용자 탭들보다 앞)", tab_ids[0] == "login" and tab_ids[1] == "admin", tab_ids[:2])
check("3 관리자 탭은 처음에 숨김", admin_tab.visible == False)

select_fn = None
for dep in demo.fns.values():
	for target_id, name in dep.targets:
		if target_id == admin_tab._id and name == "select":
			select_fn = dep.fn.__name__
check("4 관리자 탭 선택 → on_refresh 연결 (로그인하면 바로 갱신)", select_fn == "on_refresh", select_fn)

admin_result = on_login("admin", "1234")
check("5 admin 로그인: 관리자 탭 보임, 사용자 탭 4개 숨김", admin_result[6]["visible"] == True and admin_result[2]["visible"] == False and admin_result[5]["visible"] == False)
check("6 admin 로그인: 관리자 탭으로 이동", admin_result[8].selected == "admin", admin_result[8].selected)

user_result = on_login("kim", "1234")
check("7 일반 사용자 로그인: 관리자 탭 숨김, 보고서 탭으로 이동", user_result[6]["visible"] == False and user_result[8].selected == "report", user_result[8].selected)

print(f"\n통과 {passed}/{total}")

# ----------------------------------------------------------------
# 4. 2부: 앱 띄우기
# ----------------------------------------------------------------
demo.launch(theme=gr.themes.Soft())