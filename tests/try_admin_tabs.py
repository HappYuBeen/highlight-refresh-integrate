"""관리자는 관리자 탭만 보이기 테스트 (임세준) - 커밋하지 않는 파일

진짜 on_login()과 build_app()을 그대로 쓰고, DB·그래프·LLM이 필요한 서비스만 가짜로 바꾼다.
가짜 로그인: 아이디가 admin으로 시작하면 관리자, 그 밖에는 일반 사용자로 로그인된다.
1부: on_login() 확인 (✅/❌) → 2부: 앱을 띄워서 관리자·일반 사용자로 직접 로그인해 보기

실행 (레포 맨 위 폴더에서):
	poetry run python -m tests.try_admin_tabs
	→ 브라우저에서 http://127.0.0.1:7860 (끝내려면 터미널에서 Ctrl+C)
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


def fake_upload_report(path, user_id):
	"""가짜 업로드: 단계 3개를 0.3초씩 보여 주고, 보고서 목록에 새 보고서를 추가한다"""
	for step in ["validate", "load_text", "persist"]:
		time.sleep(0.3)
		yield {"type": "progress", "step": step}
	REPORTS.append(FakeReport(2, "반도체 산업 동향"))
	yield {"type": "result", "report_id": 2, "counts": {"text": 20, "table": 3, "chart": 1}, "warnings": []}


class FakeUser:
	user_id = "admin"
	name = "관리자"
	role = "admin"
	created_at = datetime(2026, 10, 1)


def fake_login(user_id, password):
	"""가짜 로그인: 아이디가 admin으로 시작하면 관리자, 아니면 일반 사용자"""
	user = FakeUser()
	user.user_id = user_id
	if user_id.startswith("admin"):
		user.role = "admin"
	else:
		user.role = "user"
	return user


class FakeSummary:
	three_lines = ["성장률 1.9% 전망", "수출 회복", "물가 안정"]
	sections = []


make_fake_module("hri.graph.registry", get_ingest_graph=lambda: None, get_qa_graph=lambda: None, get_summary_graph=lambda: None, get_agent_graph=lambda: None)
make_fake_module("hri.config.settings", load_settings=lambda: None)
make_fake_module("hri.service.report_service", search_reports=fake_search_reports)
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

# ----------------------------------------------------------------
# 2. 1부: on_login 확인
# ----------------------------------------------------------------
from hri.view import app  # noqa: E402
from hri.view.user_view import on_login  # noqa: E402

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


TAB_NAMES = ["보고서", "요약", "질의응답", "내 기록", "관리자"]


def shown_tabs(result):
	"""on_login 결과(값 11개)에서 보이게 되는 탭 이름만 모으기 (2~6번째 값이 보고서~관리자 탭)"""
	shown = []
	for i in range(5):
		if result[i + 2]["visible"] == True:
			shown.append(TAB_NAMES[i])
	return shown


result = on_login("admin01", "pw")
check("1 관리자: 관리자 탭만 보임", shown_tabs(result) == ["관리자"], shown_tabs(result))
check("2 관리자: 로그인 후 관리자 탭으로 이동", result[8].selected == "admin", result[8].selected)
check("3 관리자: 로그인 탭 숨김·로그아웃 버튼 보임", result[1]["visible"] == "hidden" and result[7]["visible"] == True)

result = on_login("userA", "pw")
check("4 일반 사용자: 보고서·요약·질의응답·내 기록 탭만 보임", shown_tabs(result) == ["보고서", "요약", "질의응답", "내 기록"], shown_tabs(result))
check("5 일반 사용자: 로그인 후 보고서 탭으로 이동", result[8].selected == "report", result[8].selected)
check("6 돌려주는 값은 그대로 11개", len(result) == 11, len(result))

print(f"\n통과 {passed}/{total}")

# ----------------------------------------------------------------
# 3. 2부: 앱 띄우기
# ----------------------------------------------------------------
demo = app.build_app()
demo.launch(theme=gr.themes.Soft())