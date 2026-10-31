"""질의응답 탭의 추천 질문 직접 눌러 보는 테스트 (류현수) - 커밋하지 않는 파일

진짜 app.py(build_app)·user_view(로그인)·chat_view(질의응답 탭, 추천 질문)는 그대로 쓰고,
DB·Chroma가 필요한 부분(회원·보고서 서비스, 그래프, 질의응답)과 아직 없는 다른 팀원 화면만 가짜로 바꿔서 앱을 띄운다.
API 키·DB 없이 돌아간다.

실행 (레포 맨 위 폴더에서):
	poetry run python -m tests.try_suggest
	→ 브라우저에서 http://127.0.0.1:7860

테스트 계정 (가짜 DB): kim / 1234
확인 순서
	1) 로그인 → 질의응답 탭: 질문 입력칸 아래에 시작 질문 3개가 보인다
	2) 시작 질문 버튼을 누르면 질문 입력칸에 그 질문이 들어간다 (바로 보내지지는 않음)
	3) 전송하면 가짜 답변이 나오고, 버튼이 후속 질문 3개로 바뀐다 → 눌러서 다시 입력칸에 들어가는지 확인
	4) '안녕'처럼 인사를 보내면 잡담 답변이라 후속 질문이 없어 버튼이 숨겨진다
"""
import sys
import types
from datetime import datetime

import gradio as gr

from hri.exception.errors import LoginFailedError

def make_fake_module(name):
	"""이름만 있는 빈 가짜 모듈을 만들어서 등록"""
	module = types.ModuleType(name)
	sys.modules[name] = module
	return module


# ----------------------------------------------------------------
# 1. 가짜 회원·보고서 서비스 (DB 대신)
# ----------------------------------------------------------------
class FakeUser:
	def __init__(self, user_id, name, role):
		self.user_id = user_id
		self.name = name
		self.role = role
		self.created_at = datetime.now()


def fake_login(user_id, password):
	if (user_id, password) != ("kim", "1234"):
		raise LoginFailedError()
	return FakeUser("kim", "김사용", "user")


user_service = make_fake_module("hri.service.user_service")
user_service.login = fake_login
user_service.register = lambda user_id, password, name: True


class FakeReport:
	def __init__(self, report_id, title):
		self.report_id = report_id
		self.title = title


report_service = make_fake_module("hri.service.report_service")
report_service.search_reports = lambda keyword="", limit=None: [FakeReport(1, "한국은행 보고서"), FakeReport(2, "KDI 전망")]


# ----------------------------------------------------------------
# 2. 가짜 그래프 (main()이 예열로 부름, Chroma·DB 필요)
# ----------------------------------------------------------------
registry = make_fake_module("hri.graph.registry")
registry.get_ingest_graph = lambda: None
registry.get_qa_graph = lambda: None
registry.get_summary_graph = lambda: None
registry.get_agent_graph = lambda: None


# ----------------------------------------------------------------
# 3. 다른 팀원 화면 (가짜, 빈 탭만)
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
	report_view.report_list_components.update(table=table, listed_ids=listed_ids, message=message)
	return panel, go_summary_btn, go_chat_btn


def fake_build_upload_tab():
	with gr.Tab("업로드") as tab:
		gr.Markdown("※ 가짜 화면 (업로드 자리)")
	return tab


report_view.build_report_list_panel = fake_build_report_list_panel
report_view.build_upload_tab = fake_build_upload_tab
report_view.on_view_summary = lambda state: (gr.update(selected="summary"), gr.update())
report_view.on_go_chat = lambda state: (gr.update(selected="chat"), gr.update())
report_view.on_report_select = lambda listed_ids, state: (state, "")


def make_fake_tab(module_name, func_name, title, tab_id, components_name=None):
	"""빈 탭을 만드는 가짜 build_*_tab 함수 등록"""
	module = make_fake_module(module_name)
	if components_name is not None:
		setattr(module, components_name, {})

	def build():
		with gr.Tab(title, id=tab_id, visible=False) as tab:
			gr.Markdown(f"※ 가짜 화면 ({func_name} 자리)")
			if components_name is not None:
				dropdown = gr.Dropdown(choices=[], multiselect=True, label="보고서 선택")
				getattr(module, components_name)["report_dropdown"] = dropdown
		return tab

	setattr(module, func_name, build)


make_fake_tab("hri.view.summary_view", "build_summary_tab", "요약·분석", "summary", "summary_components")
# 내 기록 탭: app.py가 '대화 이어가기' 버튼(restore_btn)·세션 드롭다운을 연결하므로 그 부품까지 만든다
history_view = make_fake_module("hri.view.history_view")
history_view.history_components = {}
history_view.on_restore = lambda session_id, state: (gr.update(), state)


def fake_build_history_tab():
	with gr.Tab("내 기록", id="history", visible=False) as tab:
		gr.Markdown("※ 가짜 화면 (build_history_tab 자리)")
		session_dropdown = gr.Dropdown(choices=[], label="대화 선택")
		restore_btn = gr.Button("대화 이어가기")
	history_view.history_components.update(session_dropdown=session_dropdown, restore_btn=restore_btn)
	return tab


history_view.build_history_tab = fake_build_history_tab
make_fake_tab("hri.view.admin_view", "build_admin_tab", "관리자", "admin")

indicator_view = make_fake_module("hri.view.indicator_view")
indicator_view.on_compare = lambda report_ids: ("※ 가짜 비교표", "", None)


# ----------------------------------------------------------------
# 4. 진짜 질의응답 탭 (chat_view) + 질의응답만 가짜 답변
# ----------------------------------------------------------------
from hri.domain.schemas import AnswerResult  # noqa: E402
from hri.view import chat_view  # noqa: E402

GREETINGS = ("안녕", "고마워", "감사")


def fake_ask_stream(question, **kwargs):
	"""질의응답 그래프 대신 정해 둔 답변을 흘려보낸다. 인사면 잡담(후속 질문 없음), 그 밖에는 후속 질문 3개."""
	if question.strip().startswith(GREETINGS):
		answer = "안녕하세요! 보고서를 선택한 뒤 궁금한 점을 물어보세요."
		yield from answer
		yield AnswerResult(question=question, answer=answer, route="chat")
		return
	answer = f"(가짜 답변) '{question}'에 대한 답변 자리입니다."
	yield from answer
	yield AnswerResult(
		question=question, answer=answer, route="rag", log_id=None,
		suggestions=[f"{question} - 후속 질문 {n}" for n in (1, 2, 3)],
	)


chat_view._ask_stream = fake_ask_stream


# ----------------------------------------------------------------
# 5. 가짜를 다 끼운 다음에 진짜 app.py를 불러온다 (순서 중요!)
# ----------------------------------------------------------------
from hri.view.app import main  # noqa: E402

if __name__ == "__main__":
	main()
