"""업로드·요약 버튼 연결 테스트 (임세준) - 커밋하지 않는 파일

진짜 build_app()(app.py)과 진짜 화면 함수(on_upload·on_summarize·on_verify·refresh_report_choices)를 그대로 쓰고,
DB·그래프·LLM이 필요한 서비스만 가짜로 바꾼다.
1부: build_app()의 버튼 연결 확인 (✅/❌) → 2부: 앱을 띄워서 직접 눌러 보기

실행 (레포 맨 위 폴더에서):
	poetry run python -m tests.try_app_wiring
	→ 브라우저에서 http://127.0.0.1:7860 (아이디·비밀번호 아무거나, 끝내려면 Ctrl+C)
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
	REPORTS.append(FakeReport(len(REPORTS) + 1, "반도체 산업 동향"))
	yield {"type": "result", "report_id": len(REPORTS), "counts": {"text": 20, "table": 3, "chart": 1}, "warnings": []}


class FakeUser:
	user_id = "admin"
	name = "관리자"
	role = "admin"
	created_at = datetime(2026, 10, 1)


def fake_login(user_id, password):
	return FakeUser()


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
make_fake_module("hri.service.history_service", restore_session=lambda session_id, user_id: [])
make_fake_module("hri.service.indicator_service", build_compare_table=lambda ids: "", plot_indicators=lambda ids: None, PLOT_DIR=None, _use_korean_font=lambda: None)
make_fake_module("hri.llm.llm_client", cost_korean_won=lambda usage, rate: 0.0, get_usd_krw=lambda: 1400.0)
make_fake_module("hri.dao.qa_log_dao", update_feedback=lambda log_id, feedback: True)
make_fake_module("hri.dao.dashboard_dao", **{name: (lambda: []) for name in [
	"select_daily_counts", "select_feedback_ratio", "select_no_source_questions", "select_perf_cost",
	"select_persona_ratio", "select_recent_errors", "select_source_ratio", "select_top_questions"]})

# ----------------------------------------------------------------
# 2. 1부: build_app()의 버튼 연결 확인
# ----------------------------------------------------------------
from hri.view import app  # noqa: E402
from hri.view.chat_view import chat_components  # noqa: E402
from hri.view.report_view import upload_components  # noqa: E402
from hri.view.summary_view import summary_components  # noqa: E402

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


demo = app.build_app()


def find_event(component, event_name):
	"""component의 event_name(click·select) 연결을 찾아서 (함수 이름, 입력 id 목록, 출력 id 목록)으로 돌려준다"""
	for dep in demo.fns.values():
		for target_id, name in dep.targets:
			if target_id == component._id and name == event_name:
				inputs = []
				for block in dep.inputs:
					inputs.append(block._id)
				outputs = []
				for block in dep.outputs:
					outputs.append(block._id)
				return dep.fn.__name__, inputs, outputs
	return None, [], []


u = upload_components
s = summary_components
c = chat_components

name, inputs, outputs = find_event(u["upload_btn"], "click")
check("1 업로드 버튼 → on_upload 연결", name == "on_upload", name)
check("2 on_upload 입력: [파일, state]", len(inputs) == 2 and inputs[0] == u["file"]._id)
check("3 on_upload 출력: [결과, 요약 탭 드롭다운, 질의응답 탭 드롭다운]", outputs == [u["result"]._id, s["report_dropdown"]._id, c["report_dropdown"]._id])

name, inputs, outputs = find_event(s["tab"], "select")
check("4 요약·분석 탭 열기 → refresh_report_choices 연결 (드롭다운 채우기)", name == "refresh_report_choices" and outputs == [s["report_dropdown"]._id], name)

name, inputs, outputs = find_event(s["three_lines_btn"], "click")
check("5 3줄 요약하기 버튼 → on_summarize_three_lines 연결", name == "on_summarize_three_lines" and inputs == [s["report_dropdown"]._id] and outputs == [block._id for block in s["page_outputs"]], name)

name, inputs, outputs = find_event(c["verify_btn"], "click")
check("6 수치 검증 버튼 → on_verify 연결", name == "on_verify" and outputs == [c["chatbot"]._id, c["message"]._id], name)

name, inputs, outputs = find_event(c["tab"], "select")
check("7 기존 연결 유지: 질의응답 탭 열기 → refresh_report_choices", name == "refresh_report_choices", name)

print(f"\n통과 {passed}/{total}")

# ----------------------------------------------------------------
# 3. 2부: 앱 띄우기
# ----------------------------------------------------------------
demo.launch(theme=gr.themes.Soft())