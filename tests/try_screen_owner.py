"""다른 사용자 로그인 시 화면 비우기 테스트 (임세준) - 커밋하지 않는 파일

진짜 build_app()과 clear_other_user_screen()을 그대로 쓰고,
DB·그래프·LLM이 필요한 서비스만 가짜로 바꾼다. (가짜 로그인은 입력한 아이디로 로그인됨)
1부: clear_other_user_screen() 확인 (✅/❌) → 2부: 앱을 띄워서 A → A / A → B 직접 해 보기

실행 (레포 맨 위 폴더에서):
	poetry run python -m tests.try_screen_owner
	→ 브라우저에서 http://127.0.0.1:7860 (아이디는 아무거나, 비밀번호 아무거나, 끝내려면 Ctrl+C)
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


def fake_search_reports(keyword="", limit=None):
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
	"""가짜 로그인: 입력한 아이디 그대로 관리자 계정으로 로그인"""
	user = FakeUser()
	user.user_id = user_id
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
# 2. 1부: clear_other_user_screen 확인
# ----------------------------------------------------------------
from hri.view import app  # noqa: E402
from hri.view.app import clear_other_user_screen  # noqa: E402

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


def is_keep(value):
	"""gr.update()(아무것도 바꾸지 않음)인지 확인"""
	return isinstance(value, dict) and list(value.keys()) == ["__type__"]


def all_keep(result):
	for value in result[1:]:
		if not is_keep(value):
			return False
	return True


user_a = {"user": {"user_id": "userA"}}
user_b = {"user": {"user_id": "userB"}}
no_user = {"user": None}

result = clear_other_user_screen(user_a, None)
check("1 처음 로그인: 화면 그대로, 주인 = A", len(result) == 16 and result[0] == "userA" and all_keep(result), result[0])

result = clear_other_user_screen(user_a, "userA")
check("2 A → 로그아웃 → A: 화면 그대로", result[0] == "userA" and all_keep(result))

result = clear_other_user_screen(user_b, "userA")
check("3 A → 로그아웃 → B: 주인 = B", result[0] == "userB", result[0])
check("4 A → B: 대화창 [], 질문·출처·요약·요청 결과 빈칸", result[1] == [] and result[2] == "" and result[3] == "" and result[8] == "" and result[9] == "" and result[10] == "")
check("5 A → B: 👍/👎 숨김, 보고서 선택 비움", result[4]["visible"] == False and result[5]["visible"] == False and result[6]["value"] == [] and result[7]["value"] == [])
check("6 A → B: 기록 표·대화 선택·업로드 결과·비교표·차트 비움", result[11] == "" and result[12]["choices"] == [] and result[13] == "" and result[14] == "" and result[15] == None)

result = clear_other_user_screen(no_user, "userA")
check("7 로그인 실패: 아무것도 안 바꾸고 주인 A 유지", result[0] == "userA" and all_keep(result))

print(f"\n통과 {passed}/{total}")

# ----------------------------------------------------------------
# 3. 2부: 앱 띄우기
# ----------------------------------------------------------------
demo = app.build_app()
demo.launch(theme=gr.themes.Soft())