"""메인 화면 · build_app, main, init_state (임세준)

앱의 전체 화면 틀을 만들고 실행하는 파일.
각 담당자가 만든 탭 함수를 불러와서 하나의 화면으로 합친다.
실행: 프로젝트 맨 위 폴더에서 python app.py
"""

import uuid
import gradio as gr

from hri.config.settings import load_settings
from hri.graph.registry import get_ingest_graph, get_qa_graph, get_summary_graph, get_agent_graph
from hri.view.user_view import build_login_tab, on_login, on_logout, login_components
from hri.view.report_view import build_upload_tab, build_report_list_panel, on_view_summary, on_go_chat, on_report_search, on_report_select, on_upload, report_list_components, upload_components
from hri.view.summary_view import build_summary_tab, on_summarize, summary_components, on_merge
from hri.view.chat_view import build_chat_tab, chat_components, connect_chat_events, refresh_report_choices
from hri.view.indicator_view import on_compare
from hri.view.history_view import build_history_tab, history_components, on_load_history, on_restore
from hri.view.admin_view import build_admin_tab, admin_components, on_refresh


# 화면 전체 테마 (UI 레퍼런스: 옅은 회색 배경·파란 강조색·둥근 모서리, CSS 없이 Gradio 테마 옵션만 사용)
APP_THEME = gr.themes.Soft(
	primary_hue="blue",
	neutral_hue="slate",
	radius_size="lg",
	font=[gr.themes.GoogleFont("Noto Sans KR"), "sans-serif"],
)


def init_state():
	"""
	처음 상태(gr.State에 넣을 값) 만들기

	로그인 전 화면에서 쓰는 빈 상태를 만든다.
	- user: 로그인한 사람 정보 (로그인 전에는 None)
	- session_id: 대화 묶음 번호 (uuid4로 새로 만듦)
	- report_ids: 선택한 보고서 번호 목록 (처음에는 빈 리스트)
	- last_log_id: 마지막 질문 기록 번호 (처음에는 None)

	입력: 없음
	출력: dict (위 4개 키를 가진 딕셔너리)
	"""
	state = {
		"user": None,
		"session_id": str(uuid.uuid4()),
		"report_ids": [],
		"last_log_id": None
	}
	return state

def clear_other_user_screen(state, screen_owner):
	"""
	다른 사람이 로그인했을 때 이전 사용자의 화면 내용 지우기

	로그아웃해도 화면 부품의 내용(대화창·요약 결과 등)은 남아 있다.
	그래서 로그인할 때마다 "지금 화면 내용이 누구 것인지(screen_owner)"를 보고,
	다른 사람이 로그인했으면 화면 내용을 비우고, 같은 사람이 다시 로그인했으면 그대로 둔다.

	순서
	1. 로그인에 실패했으면(state의 user가 None) 아무것도 바꾸지 않는다
	2. 화면 주인이 없거나(처음 로그인) 같은 사람이면 화면을 그대로 두고 주인만 기록한다
	3. 다른 사람이면 질의응답·요약·내 기록·업로드·지표 비교 화면 내용을 비운다

	- 질문 기록 자체는 DB(qa_logs)에 남아 있어서, 다시 로그인하면 내 기록 탭에서 언제든 볼 수 있다
	- 돌려주는 값 15개는 build_app()의 outputs 순서와 같아야 한다
	  [화면 주인, 대화창, 질문 입력칸, 출처, 👍, 👎, 질의응답 보고서 선택,
	   요약 보고서 선택, 요약 결과, 요청, 요청 결과,
	   기록 표, 대화 선택, 업로드 결과, 비교표, 비교 차트]

	입력: state (gr.State), screen_owner (지금 화면 내용의 주인 user_id, 처음에는 None)
	출력: list (값 15개)
	"""
	keep = gr.update()

	# 1) 로그인 실패면 그대로
	if state["user"] == None:
		return [screen_owner] + [keep] * 14

	user_id = state["user"]["user_id"]

	# 2) 처음 로그인이거나 같은 사람이면 화면 그대로
	if screen_owner == None or screen_owner == user_id:
		return [user_id] + [keep] * 14

	# 3) 다른 사람이면 화면 내용 비우기
	return [
		user_id,  # 화면 주인
		[],  # 대화창
		"",  # 질문 입력칸
		"",  # 출처
		gr.update(visible=False),  # 👍
		gr.update(visible=False),  # 👎
		gr.update(value=[]),  # 질의응답 보고서 선택
		gr.update(value=[]),  # 요약 보고서 선택
		"",  # 요약 결과
		"",  # 병합 결과
		"",  # 기록 표 (내 기록 탭을 열면 다시 채워짐)
		gr.update(choices=[], value=None),  # 대화 선택
		"",  # 업로드 결과
		"",  # 비교표
		None  # 비교 차트
	]


def build_app():
	"""
	메인 화면 만들기

	gr.Blocks 안에 제목·로그아웃 버튼과 탭 6개를 gr.Tabs로 배치한다.
	- 로그인 탭: build_login_tab() (처음에는 이 탭만 보임)
	- 보고서 탭: 안에 작은 탭 3개
		- 보고서 목록·검색: build_report_list_panel()
		- 지표 비교표: 비교할 보고서 드롭다운 + 비교 버튼 + 비교표 + 차트
		- 업로드: build_upload_tab()
	- 요약·분석, 질의응답, 내 기록, 관리자 탭: 각 담당자의 build_*_tab()
	  (로그인 전에는 숨김, 관리자 탭은 관리자만 보임)

	버튼 연결
	- 로그인 버튼 → on_login (탭 보이기, 로그아웃 버튼 보이기, 질의응답 탭 선택, 비교 보고서 목록 채우기)
	- 로그아웃 버튼 → on_logout (로그인 탭만 남기기)
	- 요약 보기 · 질의응답으로 이동 버튼 → on_view_summary · on_go_chat (탭 이동 + 보고서 드롭다운 바꾸기)
	- 지표 추출·비교 버튼 → on_compare (비교표, 차트 보여주기)
	- 업로드 버튼 → on_upload (진행 상황·결과 보여주기, 요약·질의응답 탭 보고서 드롭다운 갱신)
	- 요약·분석 탭을 열면 → refresh_report_choices (보고서 드롭다운 채우기, 질의응답 탭과 같은 함수)
	- 요약하기 버튼 → on_summarize (선택한 보고서 요약 보여주기)
	- 요청 실행 버튼 → on_request (자연어 요청 결과·실행한 도구 보여주기)
	  (업로드·요약 버튼은 다른 탭의 드롭다운이나 state가 필요해서 모든 탭을 만든 뒤 여기서 연결한다)
	- 내 기록 탭을 열면 → on_load_history (대화별 기록 표·대화 선택 목록 채우기)
	- 로그인 뒤 → clear_other_user_screen (다른 사람이 로그인했으면 이전 사용자의 화면 내용 비우기)

	입력: 없음
	출력: gr.Blocks (만든 화면)
	"""
	with gr.Blocks(title="HRI Insight Bridge") as demo:
		state = gr.State(init_state())
		# 지금 화면 내용이 누구 것인지 기억 (로그아웃해도 지우지 않음)
		screen_owner = gr.State(None)

		# 제목, 로그아웃 버튼 (로그아웃 버튼은 처음에 숨김)
		with gr.Row():
			# 제목은 gr.HTML로 둔다 (gr.Markdown은 칸에 스크롤이 생겨서 Windows에서 위아래 화살표가 보임)
			gr.HTML("<h2>📊 HRI Insight Bridge - 경제분석보고서 AI 요약·질의응답</h2>")
			logout_btn = gr.Button("로그아웃", size="sm", scale=0, visible=False)

		with gr.Tabs(selected="login") as tabs:
			# 로그인 탭
			login_tab = build_login_tab()

			# 관리자 탭 (관리자 로그인 때 선택이 되려면 숨겨진 탭들보다 앞에 있어야 해서 로그인 탭 바로 뒤에 둠)
			admin_tab = build_admin_tab()			

			# 보고서 탭
			with gr.Tab("보고서", id="report", visible=False) as report_tab:
				with gr.Tabs():
					# 보고서 목록·검색
					with gr.Tab("보고서 목록·검색", id="report_list"):
						report_list, go_summary_btn, go_chat_btn = build_report_list_panel(state)

					# 지표 비교표
					with gr.Tab("지표 비교표", id="compare"):
						compare_reports = gr.Dropdown(label="비교할 보고서(2개 이상)", choices=[], multiselect=True)
						compare_btn = gr.Button("지표 추출·비교", variant="primary")
						compare_table = gr.Markdown()
						compare_chart = gr.Image(label="비교 차트")

					# 보고서 업로드
					build_upload_tab()

			# 요약·분석, 질의응답, 내 기록, 관리자 탭
			summary_tab = build_summary_tab()
			chat_tab = build_chat_tab()
			history_tab = build_history_tab()
		# 질의응답 탭 버튼 연결 (state가 필요해서 build_app에서 부름)
		connect_chat_events(state)

		# 로그인 버튼 누르면 로그인 → 다른 사람이 로그인했으면 이전 사용자의 화면 내용 비우기
		login_components["login_btn"].click(
			fn=on_login,
			inputs=[login_components["user_id"], login_components["password"]],
			outputs=[state, login_tab, report_tab, summary_tab, chat_tab, history_tab, admin_tab, logout_btn, tabs, compare_reports, login_components["message"]]
		).then(
			fn=clear_other_user_screen,
			inputs=[state, screen_owner],
			outputs=[
				screen_owner,
				chat_components["chatbot"], chat_components["message"], chat_components["sources"],
				chat_components["like_btn"], chat_components["dislike_btn"], chat_components["report_dropdown"],
				summary_components["report_dropdown"], summary_components["summary_result"], summary_components["merge_result"],
				history_components["table"], history_components["session_dropdown"],
				upload_components["result"],
				compare_table, compare_chart
			]
		)

		# 로그아웃 버튼 누르면 로그아웃
		logout_btn.click(
			fn=on_logout,
			inputs=state,
			outputs=[state, login_tab, report_tab, summary_tab, chat_tab, history_tab, admin_tab, logout_btn, tabs, compare_reports]
		)

		# 보고서 탭을 열면 보고서 목록 불러오기 (목록 메뉴가 처음부터 펼쳐져 있어서 펼칠 때 대신 여기서 불러옴)
		report_tab.select(
			fn=on_report_search,
			inputs=[report_list_components["keyword"], state],
			outputs=[report_list_components["table"], report_list_components["listed_ids"], report_list_components["message"]]
		)

		# 보고서 목록 표에서 줄을 누르면 그 보고서를 선택
		report_list_components["table"].select(
			fn=on_report_select,
			inputs=[report_list_components["listed_ids"], state],
			outputs=[state, report_list_components["message"]]
		)

		# 요약 보기 버튼 누르면 요약 탭으로
		go_summary_btn.click(
			fn=on_view_summary,
			inputs=state,
			outputs=[tabs, summary_components["report_dropdown"]]
		)

		# 내 기록 탭을 열면 기록 표·대화 선택 목록 채우기
		history_tab.select(
			fn=on_load_history,
			inputs=state,
			outputs=[history_components["table"], history_components["session_dropdown"]]
		)
		
		# 대화 이어가기 버튼 누르면 질의응답 탭 Chatbot에 이전 대화 채우기
		history_components["restore_btn"].click(
			fn=on_restore,
			inputs=[history_components["session_dropdown"], state],
			outputs=[chat_components["chatbot"], state]
		)		

		# 질의응답으로 이동 버튼 누르면 질의응답 탭으로
		go_chat_btn.click(
			fn=on_go_chat,
			inputs=state,
			outputs=[tabs, chat_components["report_dropdown"]]
		)

		# 지표 추출·비교 버튼 누르면 비교표, 차트 보여주기
		compare_btn.click(
			fn=on_compare,
			inputs=compare_reports,
			outputs=[compare_table, compare_chart]
		)

		# 업로드 버튼 누르면 진행 상황·결과를 보여주고, 요약·질의응답 탭 보고서 드롭다운 갱신
		upload_components["upload_btn"].click(
			fn=on_upload,
			inputs=[upload_components["file"], state],
			outputs=[upload_components["result"], summary_components["report_dropdown"], chat_components["report_dropdown"]]
		)

		# 관리자 탭 업로드 버튼: 진행 상황·결과를 보여 주고, 끝나면 대시보드(보고서 목록 등) 갱신
		admin_components["upload_btn"].click(
			fn=on_upload,
			inputs=[admin_components["file"], state],
			outputs=[admin_components["result"], summary_components["report_dropdown"], chat_components["report_dropdown"]]
		).then(
			fn=on_refresh,
			outputs=admin_components["outputs"]
		)

		# 요약·분석 탭을 열면 보고서 드롭다운 채우기 (질의응답 탭과 같은 refresh_report_choices 사용)
		summary_components["tab"].select(
			fn=refresh_report_choices,
			inputs=[state, summary_components["report_dropdown"]],
			outputs=summary_components["report_dropdown"]
		)

		# 요약하기 버튼 누르면 선택한 보고서 요약 보여주기
		summary_components["summarize_btn"].click(
			fn=on_summarize,
			inputs=[summary_components["report_dropdown"],summary_components["view_radio"]],
			outputs=summary_components["summary_result"]
		)

		# 병합하기 버튼 누르면 선택한 보고서를 병합해서 보여주기
		summary_components["merge_btn"].click(
			fn=on_merge,
			inputs=summary_components["report_dropdown"],
			outputs=summary_components["merge_result"]
		)

	return demo


def main():
	"""
	앱 실행하기

	순서
	1. load_settings()로 .env 설정을 읽는다
	2. 그래프 4개(업로드·질의응답·요약·에이전트)를 미리 한 번씩 만들어 둔다 (예열)
	   → 처음 쓰는 사람이 기다리지 않게 하려고
	3. build_app()으로 화면을 만들고 http://localhost:7860 에서 실행한다

	입력: 없음
	출력: 없음
	"""
	# 설정 읽기
	load_settings()

	# 그래프 미리 만들기
	get_ingest_graph()
	get_qa_graph()
	get_summary_graph()
	get_agent_graph()

	# 화면 띄우기
	demo = build_app()
	demo.launch(server_port=7860, theme=APP_THEME)