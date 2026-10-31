"""로그인 화면 직접 눌러 보는 테스트 (임세준) - 커밋하지 않는 파일

진짜 user_view.py(build_login_tab, on_login, on_register, on_logout)는 그대로 쓰고,
DB가 필요한 부분(회원 서비스, 보고서 목록)과 다른 팀원 화면만 가짜로 바꿔서 앱을 띄운다.

실행 (레포 맨 위 폴더에서):
	poetry run python -m tests.try_login
	→ 브라우저에서 http://127.0.0.1:7860

테스트 계정 (가짜 DB):
	일반 사용자  kim / 1234
	관리자      admin / admin123
"""
import sys
import types
from datetime import datetime

import gradio as gr

from hri.exception.errors import LoginFailedError, DuplicateUserError


def make_fake_module(name):
	"""이름만 있는 빈 가짜 모듈을 만들어서 등록"""
	module = types.ModuleType(name)
	sys.modules[name] = module
	return module


# ----------------------------------------------------------------
# 1. 가짜 회원 서비스 (DB 대신 딕셔너리)
# ----------------------------------------------------------------
class FakeUser:
	def __init__(self, user_id, name, role):
		self.user_id = user_id
		self.name = name
		self.role = role
		self.created_at = datetime.now()


FAKE_USERS = {
	"kim": {"password": "1234", "name": "김사용", "role": "user"},
	"admin": {"password": "admin123", "name": "관리자", "role": "admin"},
}


def fake_login(user_id, password):
	if user_id not in FAKE_USERS:
		raise LoginFailedError()
	if FAKE_USERS[user_id]["password"] != password:
		raise LoginFailedError()
	info = FAKE_USERS[user_id]
	return FakeUser(user_id, info["name"], info["role"])


def fake_register(user_id, password, name):
	if user_id in FAKE_USERS:
		raise DuplicateUserError()
	FAKE_USERS[user_id] = {"password": password, "name": name, "role": "user"}
	print(f"[가짜 DB] 회원가입: {user_id}")
	return True


user_service = make_fake_module("hri.service.user_service")
user_service.login = fake_login
user_service.register = fake_register


# ----------------------------------------------------------------
# 2. 가짜 보고서 서비스 (지표 비교 목록용)
# ----------------------------------------------------------------
class FakeReport:
	def __init__(self, report_id, title):
		self.report_id = report_id
		self.title = title


report_service = make_fake_module("hri.service.report_service")
report_service.search_reports = lambda keyword="", limit=None: [
	FakeReport(1, "한국은행 보고서"),
	FakeReport(2, "KDI 전망"),
	FakeReport(3, "수출입 동향"),
]


# ----------------------------------------------------------------
# 3. 가짜 설정·그래프 (main()에서 부름)
# ----------------------------------------------------------------
settings = make_fake_module("hri.config.settings")
settings.load_settings = lambda: None

registry = make_fake_module("hri.graph.registry")
registry.get_ingest_graph = lambda: None
registry.get_qa_graph = lambda: None
registry.get_summary_graph = lambda: None
registry.get_agent_graph = lambda: None


# ----------------------------------------------------------------
# 4. 다른 팀원 화면 (가짜, 빈 탭만)
# ----------------------------------------------------------------
report_view = make_fake_module("hri.view.report_view")
report_view.report_list_components = {}


def fake_build_report_list_panel():
	with gr.Column() as panel:
		gr.Markdown("※ 가짜 화면 (보고서 목록 자리)")
		table = gr.Dataframe(value=[["한국은행 보고서"], ["KDI 전망"]], headers=["제목"], interactive=False)
		listed_ids = gr.State([1, 2])
		message = gr.Markdown()
		go_summary_btn = gr.Button("요약 보기")
		go_chat_btn = gr.Button("질의응답으로 이동")
	report_view.report_list_components["table"] = table
	report_view.report_list_components["listed_ids"] = listed_ids
	report_view.report_list_components["message"] = message
	return panel, go_summary_btn, go_chat_btn


def fake_build_upload_tab():
	with gr.Tab("업로드") as tab:
		gr.Markdown("※ 가짜 화면 (업로드 자리)")
	return tab


report_view.build_report_list_panel = fake_build_report_list_panel
report_view.build_upload_tab = fake_build_upload_tab
report_view.on_view_summary = lambda state: (gr.Tabs(selected="summary"), gr.update())
report_view.on_go_chat = lambda state: (gr.Tabs(selected="chat"), gr.update())
report_view.on_report_select = lambda listed_ids, state: (state, "")


def make_fake_tab(module_name, func_name, title, tab_id, components_name=None):
	"""빈 탭을 만드는 가짜 build_*_tab 함수 등록"""
	module = make_fake_module(module_name)
	if components_name != None:
		setattr(module, components_name, {})

	def build():
		with gr.Tab(title, id=tab_id, visible=False) as tab:
			gr.Markdown(f"※ 가짜 화면 ({func_name} 자리)")
			if components_name != None:
				dropdown = gr.Dropdown(choices=[], multiselect=True, label="보고서 선택")
				getattr(module, components_name)["report_dropdown"] = dropdown
		return tab

	setattr(module, func_name, build)


make_fake_tab("hri.view.summary_view", "build_summary_tab", "요약·분석", "summary", "summary_components")
make_fake_tab("hri.view.chat_view", "build_chat_tab", "질의응답", "chat", "chat_components")
make_fake_tab("hri.view.history_view", "build_history_tab", "내 기록", "history")
make_fake_tab("hri.view.admin_view", "build_admin_tab", "관리자", "admin")

indicator_view = make_fake_module("hri.view.indicator_view")
indicator_view.on_compare = lambda report_ids: ("※ 가짜 비교표", "", None)


# ----------------------------------------------------------------
# 5. 가짜를 다 끼운 다음에 진짜 app.py를 불러온다 (순서 중요!)
# ----------------------------------------------------------------
from hri.view.app import main  # noqa: E402

if __name__ == "__main__":
	main()