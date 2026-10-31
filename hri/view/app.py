"""메인 화면 · build_app, main, init_state (임세준)

앱의 전체 화면 틀을 만들고 실행하는 파일.
각 담당자가 만든 탭 함수를 불러와서 하나의 화면으로 합친다.
실행: 프로젝트 맨 위 폴더에서 python app.py
"""

import uuid
import gradio as gr

from hri.config.settings import load_settings
from hri.graph.registry import get_ingest_graph, get_qa_graph, get_summary_graph, get_agent_graph
from hri.view.user_view import build_login_tab, on_login, on_logout, login_components, LOGIN_BOX_STYLE
from hri.view.report_view import build_upload_tab, build_report_list_panel, on_view_summary, on_go_chat, on_report_search, on_report_select, on_upload, report_list_components, upload_components
from hri.view.summary_view import build_summary_tab, on_summarize_three_lines, on_summarize_sections, on_prev_page as on_summary_prev_page, on_next_page as on_summary_next_page, on_clear_pages, on_limit_reports, summary_components, on_merge, SUMMARY_TAB_STYLE, SCROLL_RESULT_TOP_JS
from hri.view.chat_view import build_chat_tab, chat_components, connect_chat_events, refresh_report_choices, CHAT_TITLE_STYLE
from hri.view.indicator_view import (
	on_compare_picked, new_pick, load_compare_reports, reset_compare_reports, on_pick_change,
	PICK_LABEL, COMPARE_TABLE_STYLE, COMPARE_TABLE_SCRIPT
)
from hri.view.history_view import history_components, on_load_history, on_restore, on_prev_page, on_next_page, HISTORY_TABLE_STYLE
from hri.view.admin_view import build_admin_tab, admin_components, on_refresh, on_admin_upload, ADMIN_SCROLLBAR_STYLE

# 화면 전체 테마 (UI 레퍼런스: 옅은 회색 배경·파란 강조색·둥근 모서리, CSS 없이 Gradio 테마 옵션만 사용)
APP_THEME = gr.themes.Soft(
	primary_hue="blue",
	neutral_hue="slate",
	radius_size="lg",
	font=[gr.themes.GoogleFont("Noto Sans KR"), "sans-serif"],
)


# 로그아웃 버튼을 탭 줄 오른쪽 끝에 올리는 스타일 (rem만 씀, 그리드 없음)
LOGOUT_STYLE = """
<style>
#logout-row {
	position: relative;
	height: 0;
	overflow: visible;
	margin: 0 0 -1rem 0;
	padding: 0;
}
#logout-row button {
	position: absolute;
	top: 0;
	right: 0;
	z-index: 1;  /* 뒤에 오는 탭 줄이 버튼을 덮어 클릭이 안 되지 않게 */
}
</style>
"""


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
	3. 다른 사람이면 질의응답·요약·과거 대화 기록·업로드·지표 비교 화면 내용을 비운다

	- 질문 기록 자체는 DB(qa_logs)에 남아 있어서, 다시 로그인하면 질의응답 탭의 과거 대화 기록에서 언제든 볼 수 있다
	- 돌려주는 값 17개는 build_app()의 outputs 순서와 같아야 한다
	  [화면 주인, 대화창, 질문 입력칸, 출처, 👍, 👎, 질의응답 보고서 선택,
	   요약 보고서 선택, 요약 결과, 요약 페이지 넘김 줄,
	   기록 표, 대화 선택, 업로드 결과, 비교 표, ECOS 데이터, 비교 차트, 비교 차트 안내 문구]

	입력: state (gr.State), screen_owner (지금 화면 내용의 주인 user_id, 처음에는 None)
	출력: list (값 17개)
	"""
	keep = gr.update()

	# 1) 로그인 실패면 그대로
	if state["user"] == None:
		return [screen_owner] + [keep] * 16

	user_id = state["user"]["user_id"]

	# 2) 처음 로그인이거나 같은 사람이면 화면 그대로
	if screen_owner == None or screen_owner == user_id:
		return [user_id] + [keep] * 16

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
		gr.update(visible=False),  # 요약 페이지 넘김 줄 (요약·병합 결과는 한 칸에 나옴)
		"",  # 기록 표 (과거 대화 기록 버튼을 누르면 다시 채워짐)
		gr.update(choices=[], value=None),  # 대화 선택
		"",  # 업로드 결과
		"",  # 비교 표
		"",  # ECOS 데이터
		gr.update(value=None, visible=False),  # 비교 차트
		gr.update(value="", visible=False)  # 비교 차트 안내 문구
	]


def show_history():
	"""과거 대화 기록 버튼: 채팅 칸을 숨기고 그 자리에 과거 대화 기록 칸을 보인다. 출력: (채팅 칸, 기록 칸)"""
	return gr.update(visible=False), gr.update(visible=True)


def show_chat():
	"""채팅으로 돌아가기·대화 이어가기: 기록 칸을 숨기고 채팅 칸을 다시 보인다. 출력: (채팅 칸, 기록 칸)"""
	return gr.update(visible=True), gr.update(visible=False)


def fill_first_screen(keyword, state):
	"""
	로그인 뒤 첫 화면 채우기

	로그인한 사람의 역할(role)에 맞는 첫 화면 내용을 채운다.
	- 일반 사용자: 보고서 탭의 보고서 목록 (on_report_search)
	- 관리자: 관리자 대시보드 (on_refresh)
	- 로그인 실패(state의 user가 None): 아무것도 바꾸지 않음

	- 돌려주는 값은 [보고서 목록 표, 표의 report_id 목록, 안내 문구] + 관리자 대시보드 출력 칸 순서다

	입력: keyword (보고서 검색어), state (gr.State)
	출력: list
	"""
	keep_report = [gr.update()] * 3
	keep_admin = [gr.update()] * len(admin_components["outputs"])
	user = state["user"]

	if user == None:
		return keep_report + keep_admin
	if user["role"] == "admin":
		return keep_report + list(on_refresh())
	return list(on_report_search(keyword, state)) + keep_admin


def build_app():
	"""
	메인 화면 만들기

	gr.Blocks 안에 로그아웃 버튼과 로그인 화면, 탭 줄(gr.Tabs) 2개를 배치한다.
	- 로그인 화면: build_login_tab() (탭 줄 없이 가운데 칸만 보임, 처음에는 이것만 보임)
	- 관리자 탭 줄: 관리자 탭 (관리자만 보임)
	- 사용자 탭 줄: 보고서·요약·분석·질의응답 탭 (일반 사용자만 보임, 첫 화면은 보고서 탭)
	  탭 하나하나의 visible을 바꾸면 Gradio(6.30)에서 탭을 눌러도 이동하지 않아서,
	  탭은 항상 보이게 두고 로그인·로그아웃 때 탭 줄을 통째로 보이기·숨기기 한다
	- 보고서 탭: 안에 작은 탭 3개
		- 보고서 목록·검색: build_report_list_panel()
		- 지표 비교표: 왼쪽 보고서 선택 창(요약·분석, 질의응답 탭과 같은 보고서 목록 + 비교 버튼), 오른쪽 접고 펴는 비교 차트 · 비교 표 · ECOS 데이터
		- 업로드: build_upload_tab()
	- 요약·분석, 질의응답(안에 과거 대화 기록), 관리자 탭: 각 담당자의 build_*_tab()

	버튼 연결
	- 로그인 버튼 → on_login (역할에 맞는 탭 줄 보이기, 로그아웃 버튼 보이기, 비교 보고서 목록 채우기)
	- 로그아웃 버튼 → on_logout (로그인 탭만 남기기)
	- 요약 보기 · 질의응답으로 이동 버튼 → on_view_summary · on_go_chat (탭 이동 + 보고서 드롭다운 바꾸기)
	- 지표 추출·비교 버튼 → on_compare_picked (비교 표, ECOS 데이터, 비교 차트 보여주기)
	- 로그인 뒤 · 지표 비교표 탭을 열면 → reset_compare_reports · load_compare_reports (보고서 선택 창 목록 채우기)
	- 업로드 버튼 → on_upload (진행 상황·결과 보여주기, 요약·질의응답 탭 보고서 드롭다운 갱신)
	- 요약·분석 탭을 열면 → refresh_report_choices (보고서 드롭다운 채우기, 질의응답 탭과 같은 함수)
	- 요약 탭 보고서 목록 값이 바뀌면 → on_limit_reports (최대 5개까지만 남기기)
	- 3줄 요약하기 · 섹션별 요약하기 버튼 → on_summarize_three_lines · on_summarize_sections (보고서별 요약을 결과 칸에 1페이지씩)
	- 요약 결과 이전·다음 버튼 → on_summary_prev_page · on_summary_next_page
	- 병합하기 버튼 → on_clear_pages · on_merge (요약 페이지를 비우고 같은 결과 칸에 병합 결과 보여주기)
	  (업로드·요약 버튼은 다른 탭의 드롭다운이나 state가 필요해서 모든 탭을 만든 뒤 여기서 연결한다)
	- 질의응답 탭의 과거 대화 기록 버튼 → show_history · on_load_history (채팅 칸 대신 기록 칸, 대화별 기록 표·대화 선택 목록 채우기)
	- 채팅으로 돌아가기 · 대화 이어가기 버튼 → show_chat (기록 칸을 닫고 채팅 칸 보이기)
	- 로그인 뒤 → clear_other_user_screen (다른 사람이 로그인했으면 이전 사용자의 화면 내용 비우기)
	  → reset_compare_reports (비교할 보고서 선택 창 채우기) → show_chat (채팅 칸 보이기)
	  → fill_first_screen (일반 사용자는 보고서 목록, 관리자는 대시보드 채우기)

	입력: 없음
	출력: gr.Blocks (만든 화면)
	"""
	with gr.Blocks(title="HRI Insight Bridge") as demo:
		state = gr.State(init_state())
		# 지금 화면 내용이 누구 것인지 기억 (로그아웃해도 지우지 않음)
		screen_owner = gr.State(None)

		# 로그아웃 버튼 (처음에는 숨김, 탭 줄 오른쪽 끝에 같은 줄로 보이게 CSS로 위치를 잡음)
		with gr.Row(elem_id="logout-row"):
			logout_btn = gr.Button("로그아웃", size="sm", scale=0, min_width=0, visible=False)

		# 로그인 화면 (탭 줄 없이 가운데 칸만 보임, 로그인하면 숨김)
		login_tab = build_login_tab()

		# 관리자 탭 줄 (처음에는 숨김, 관리자가 로그인하면 on_login이 보이게 함)
		with gr.Tabs(visible=False) as admin_tabs:
			build_admin_tab()

		# 사용자 탭 줄 (처음에는 숨김, 일반 사용자가 로그인하면 on_login이 보이게 함)
		with gr.Tabs(selected="report", visible=False) as tabs:
			# 보고서 탭
			with gr.Tab("보고서", id="report") as report_tab:
				with gr.Tabs():
					# 보고서 목록·검색
					with gr.Tab("보고서 목록·검색", id="report_list"):
						report_list, go_summary_btn, go_chat_btn = build_report_list_panel(state)

					# 지표 비교표
					with gr.Tab("지표 비교표", id="compare") as compare_tab:
						# 좌우 1:3 (왼쪽: 보고서 선택 창, 오른쪽: 비교 차트·비교 표·ECOS 데이터)
						with gr.Row():
							# 보고서 선택 창: 요약·분석, 질의응답 탭과 같은 보고서 목록(최대 MAX_COMPARE_REPORTS개, 넘치면 목록 안에서 스크롤)
							# 아래에 수평선 뒤 비교 버튼, 진행바가 붙음 (COMPARE_TABLE_STYLE)
							with gr.Column(scale=1, variant="panel", elem_id="compare-left"):
								compare_pick = gr.State(new_pick())
								gr.Markdown("#### 보고서")
								with gr.Column(elem_id="compare-report-body"):
									compare_reports = gr.CheckboxGroup(label=PICK_LABEL, choices=[], elem_id="compare-report-list")
								with gr.Row(elem_id="compare-actions"):
									compare_btn = gr.Button("지표 추출·비교", variant="primary", size="sm", min_width=0)
								# 비교 중 진행바를 비교 차트 대신 버튼 바로 아래에 보여 주는 자리 (평소에는 빈 칸)
								compare_progress = gr.Markdown(min_height=80)
								compare_pick_outputs = [compare_pick, compare_reports]
							# 접고 펼 수 있는 3칸 (처음에는 모두 접힘, 비교가 끝나면 비교 차트를 펼침). 펼친 칸끼리 남는 높이를 똑같이 나눔 (COMPARE_TABLE_STYLE)
							# 비교 표·ECOS 데이터는 칸 높이에 맞춰 페이지로 나눔 (COMPARE_TABLE_SCRIPT)
							with gr.Column(scale=3, elem_id="compare-right"):
								with gr.Accordion("비교 차트", open=False, elem_classes="compare-fold") as compare_chart_fold:
									# 차트가 없으면 이미지 칸은 숨기고 안내 문구만 가운데에 보여 줌 (on_compare_picked)
									compare_chart = gr.Image(show_label=False, visible=False)
									compare_chart_note = gr.Markdown(visible=False, elem_classes="compare-chart-note")
								with gr.Accordion("비교 표", open=False, elem_classes="compare-fold"):
									compare_table = gr.Markdown(elem_classes="compare-table")
								with gr.Accordion("ECOS 데이터", open=False, elem_classes="compare-fold"):
									compare_ecos = gr.Markdown(elem_classes="compare-table")

					# 보고서 업로드
					build_upload_tab()

			# 요약·분석, 질의응답 탭 (과거 대화 기록은 질의응답 탭 안에 있음)
			build_summary_tab()
			build_chat_tab()
		# 질의응답 탭 버튼 연결 (state가 필요해서 build_app에서 부름)
		connect_chat_events(state)

		# 로그인 버튼 누르면 로그인 → 다른 사람이 로그인했으면 이전 사용자의 화면 내용 비우기
		login_components["login_btn"].click(
			fn=on_login,
			inputs=[login_components["user_id"], login_components["password"]],
			outputs=[state, login_tab, tabs, admin_tabs, logout_btn, compare_reports, login_components["message"]]
		).then(
			fn=clear_other_user_screen,
			inputs=[state, screen_owner],
			outputs=[
				screen_owner,
				chat_components["chatbot"], chat_components["message"], chat_components["sources"],
				chat_components["like_btn"], chat_components["dislike_btn"], chat_components["report_dropdown"],
				summary_components["report_dropdown"], summary_components["summary_result"], summary_components["pager"],
				history_components["table"], history_components["session_dropdown"],
				upload_components["result"],
				compare_table, compare_ecos, compare_chart, compare_chart_note
			]
		).then(
			# 비교할 보고서 선택 창: 선택을 비우고 목록 채우기
			fn=reset_compare_reports,
			inputs=state,
			outputs=compare_pick_outputs
		).then(
			# 로그인하면 질의응답 탭은 항상 채팅 칸부터 보이게 (이전 사용자가 열어 둔 과거 대화 기록 칸 닫기)
			fn=show_chat,
			outputs=[chat_components["chat_col"], history_components["column"]]
		).then(
			# 첫 화면 채우기 (탭을 고르지 않고 바로 보이므로 탭 선택 이벤트 대신 여기서 채움)
			fn=fill_first_screen,
			inputs=[report_list_components["keyword"], state],
			outputs=[report_list_components["table"], report_list_components["listed_ids"], report_list_components["message"]] + admin_components["outputs"]
		)

		# 로그아웃 버튼 누르면 로그아웃
		logout_btn.click(
			fn=on_logout,
			inputs=state,
			outputs=[state, login_tab, tabs, admin_tabs, logout_btn, compare_reports]
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

		# 질의응답 탭 '과거 대화 기록' 버튼 누르면 채팅 칸 대신 기록 칸을 보이고 기록 표·대화 선택 목록 채우기
		chat_components["history_btn"].click(
			fn=show_history,
			outputs=[chat_components["chat_col"], history_components["column"]]
		).then(
			fn=on_load_history,
			inputs=state,
			outputs=[history_components["table"], history_components["session_dropdown"], history_components["page_state"]]
		)

		# 채팅으로 돌아가기 버튼 누르면 기록 칸을 닫고 채팅 칸 보이기
		history_components["back_btn"].click(
			fn=show_chat,
			outputs=[chat_components["chat_col"], history_components["column"]]
		)

		# 과거 대화 기록 표 이전·다음 페이지 버튼
		history_components["prev_btn"].click(
			fn=on_prev_page,
			inputs=[state, history_components["page_state"]],
			outputs=[history_components["table"], history_components["page_state"]]
		)
		history_components["next_btn"].click(
			fn=on_next_page,
			inputs=[state, history_components["page_state"]],
			outputs=[history_components["table"], history_components["page_state"]]
		)

		# 대화 이어가기 버튼 누르면 Chatbot에 이전 대화를 채우고 채팅 칸으로 돌아가기
		history_components["restore_btn"].click(
			fn=on_restore,
			inputs=[history_components["session_dropdown"], state],
			outputs=[chat_components["chatbot"], state]
		).then(
			fn=show_chat,
			outputs=[chat_components["chat_col"], history_components["column"]]
		)

		# 질의응답으로 이동 버튼 누르면 질의응답 탭으로
		go_chat_btn.click(
			fn=on_go_chat,
			inputs=state,
			outputs=[tabs, chat_components["report_dropdown"]]
		)

		# 지표 추출·비교 버튼 누르면 비교 표, ECOS 데이터, 비교 차트 보여주기
		compare_btn.click(
			fn=on_compare_picked,
			inputs=compare_pick,
			outputs=[compare_table, compare_ecos, compare_chart, compare_chart_note, compare_chart_fold],
			show_progress_on=compare_progress
		)

		# 보고서 선택 창: 탭을 열면 목록 다시 읽기, 선택 칸 누르면 선택 반영
		compare_tab.select(fn=load_compare_reports, inputs=[state, compare_pick], outputs=compare_pick_outputs)
		compare_reports.input(fn=on_pick_change, inputs=[compare_reports, compare_pick], outputs=compare_pick_outputs)

		# 업로드 버튼 누르면 진행 상황·결과를 보여주고, 요약·질의응답 탭 보고서 드롭다운 갱신
		upload_components["upload_btn"].click(
			fn=on_upload,
			inputs=[upload_components["file"], state],
			outputs=[upload_components["result"], summary_components["report_dropdown"], chat_components["report_dropdown"]]
		)

		# 관리자 탭 업로드 버튼: 진행 상황·결과를 파일 하나씩 페이지로 보여 주고(파일이 끝날 때마다 보고서 목록 갱신), 끝나면 대시보드 갱신
		admin_components["upload_btn"].click(
			fn=on_admin_upload,
			inputs=[admin_components["file"], state],
			outputs=[
				admin_components["result"], admin_components["page_info"], admin_components["page_state"], admin_components["page_buttons"], admin_components["reports"],
				summary_components["report_dropdown"], chat_components["report_dropdown"]
			],
			# 업로드 중 파란 진행 표시는 결과 칸에만 (페이지 문구 위에도 생기면 선이 2개라 헷갈림)
			show_progress_on=admin_components["result"]
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

		# 요약 탭 보고서 목록 값이 바뀌면 최대 개수(5개)를 넘지 않게 맞추기 (탭 열기·요약 보기·업로드로 바뀔 때도 포함)
		summary_components["report_dropdown"].change(
			fn=on_limit_reports,
			inputs=summary_components["report_dropdown"],
			outputs=summary_components["report_dropdown"]
		)

		# 3줄 요약하기·섹션별 요약하기 버튼 누르면 선택한 보고서 요약을 결과 칸에 1페이지씩 보여주기
		summary_components["three_lines_btn"].click(
			fn=on_summarize_three_lines,
			inputs=summary_components["report_dropdown"],
			outputs=summary_components["page_outputs"]
		).then(fn=None, js=SCROLL_RESULT_TOP_JS)
		summary_components["sections_btn"].click(
			fn=on_summarize_sections,
			inputs=summary_components["report_dropdown"],
			outputs=summary_components["page_outputs"]
		).then(fn=None, js=SCROLL_RESULT_TOP_JS)

		# 요약 결과 이전·다음 페이지 버튼
		summary_components["prev_btn"].click(
			fn=on_summary_prev_page,
			inputs=[summary_components["pages_state"], summary_components["page_state"]],
			outputs=summary_components["page_outputs"]
		).then(fn=None, js=SCROLL_RESULT_TOP_JS)
		summary_components["next_btn"].click(
			fn=on_summary_next_page,
			inputs=[summary_components["pages_state"], summary_components["page_state"]],
			outputs=summary_components["page_outputs"]
		).then(fn=None, js=SCROLL_RESULT_TOP_JS)

		# 병합하기 버튼 누르면 요약 페이지를 비우고 같은 결과 칸에 병합 결과 보여주기
		summary_components["merge_btn"].click(
			fn=on_clear_pages,
			outputs=summary_components["page_outputs"]
		).then(
			fn=None, js=SCROLL_RESULT_TOP_JS
		).then(
			fn=on_merge,
			inputs=summary_components["report_dropdown"],
			outputs=summary_components["summary_result"]
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
	# 스타일은 head로 한 번에 넣는다 (화면 안에 빈 HTML 칸을 만들면 위쪽 여백이 생겨서)
	demo.launch(server_port=7860, theme=APP_THEME, footer_links=[], head=LOGIN_BOX_STYLE + HISTORY_TABLE_STYLE + LOGOUT_STYLE + CHAT_TITLE_STYLE + SUMMARY_TAB_STYLE + ADMIN_SCROLLBAR_STYLE + COMPARE_TABLE_STYLE + COMPARE_TABLE_SCRIPT)
