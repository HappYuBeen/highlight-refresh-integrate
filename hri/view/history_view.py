"""과거 대화 기록 화면 · build_history_panel, on_restore (임세준)

로그인한 사용자가 지난 질문 기록을 대화(session)별로 보고,
고른 대화를 질의응답 탭에서 이어서 질문할 수 있게 하는 화면. (질의응답 탭 안에서 채팅 칸 자리에 보인다)
- build_history_panel(): 기록 표(gr.Markdown)·대화 선택(gr.Dropdown)·'대화 이어가기'·'채팅으로 돌아가기' 버튼 배치
- on_restore(): 고른 대화를 질의응답 탭 Chatbot에 채우고 state의 session_id를 바꾼다
- 버튼 연결은 state·질의응답 탭 Chatbot이 필요해서 app.py의 build_app()에서 한다
- 그래서 build_app()에서 쓸 화면 부품을 history_components에 담아 둔다
- on_load_history(): 과거 대화 기록 버튼을 누를 때 get_history()로 기록 표·대화 선택 목록을 채운다
- on_prev_page() · on_next_page(): 기록 표를 이전·다음 페이지로 넘긴다 (PAGE_SIZE개씩)
"""
import math

import gradio as gr

from hri.loader.table_loader import table_to_markdown
from hri.service.history_service import get_history, restore_session
from hri.util.logger import safe_call


# build_app()에서 버튼을 연결할 때 쓰려고 화면 부품을 저장해 둠
history_components = {}

# 기록 표 머리글
HISTORY_HEADER = ["마지막 시각", "첫 질문", "질문 수"]

# 한 페이지에 보여 줄 대화 수 (바꾸고 싶으면 이 숫자만 고치면 됨)
PAGE_SIZE = 10

# 기록 표를 화면 너비에 맞추는 스타일 (rem만 씀, 색은 테마 그대로)
HISTORY_TABLE_STYLE = """
<style>
#history-table,
#history-table table {
	width: 100%;
}
#history-pick {
	align-items: flex-end;
}
/* 드롭다운 입력 칸(wrap-inner)과 이어가기 버튼의 높이를 같게 맞춤 */
#history-pick .wrap-inner {
	height: 2.75rem;
	min-height: 2.75rem;
}
#history-pick button {
	height: 2.75rem;
}
#history-table th,
#history-table td {
	padding: 0.35rem 0.6rem;
	font-size: 0.9rem;
}
#history-table * {
	scrollbar-width: none;
}
#history-table *::-webkit-scrollbar {
	display: none;
}
</style>
"""


def _empty_page():
	"""
	페이지 상태 빈 값 만들기

	입력: 없음
	출력: dict (user_id, 전체 대화 행 rows, 현재 page)
	"""
	return {"user_id": None, "rows": [], "page": 1}


def _make_page(user_id, table_rows, page):
	"""
	대화 목록 한 페이지 만들기

	순서
	1. 전체 페이지 수를 구하고 page가 범위를 넘으면 1 또는 마지막 페이지로 맞춘다
	2. 해당 페이지 행만 잘라 머리글과 함께 표로 만든다
	3. 표 아래에 "현재 페이지 / 전체 페이지"를 붙인다

	입력: user_id (로그인한 사람), table_rows (대화별 본문 행 전체), page (보고 싶은 페이지 번호)
	출력: tuple (표 Markdown 글, 바뀐 페이지 상태 dict)
	"""
	total_pages = max(1, math.ceil(len(table_rows) / PAGE_SIZE))
	page = min(max(page, 1), total_pages)

	start = (page - 1) * PAGE_SIZE
	rows = [HISTORY_HEADER] + table_rows[start:start + PAGE_SIZE]

	text = table_to_markdown(rows) + f"\n\n{page} / {total_pages} 페이지 (총 {len(table_rows)}개 대화)"
	return text, {"user_id": user_id, "rows": table_rows, "page": page}


def build_history_panel():
	"""
	과거 대화 기록 화면 만들기 (질의응답 탭 안, 채팅 칸 자리에 들어감)

	세션별 질문 기록을 table_to_markdown()으로 그린 gr.Markdown과
	세션 선택 gr.Dropdown·'대화 이어가기' gr.Button·'채팅으로 돌아가기' gr.Button을 배치한다.

	- build_chat_tab()이 채팅 칸 옆에 숨겨 둔 채(visible=False) 부르고,
	  소스 칸 맨 아래 '과거 대화 기록' 버튼을 누르면 채팅 칸 대신 이 칸이 보인다
	- 기록 표는 [마지막 시각, 첫 질문, 질문 수] 머리글로 그린다
	- 버튼 연결은 build_app()에서 하므로 부품을 history_components에 담아 둔다

	입력: 없음
	출력: gr.Column (과거 대화 기록 칸)
	"""
	with gr.Column(scale=6, variant="panel", visible=False, elem_id="chat-history-col") as history_col:
		gr.Markdown("#### 과거 대화 기록")
		# 이어갈 대화 고르기: 드롭다운과 이어가기 버튼을 한 줄에 (한 div)
		with gr.Row(elem_id="history-pick"):
			session_dropdown = gr.Dropdown(label="대화 선택", choices=[], scale=4)
			restore_btn = gr.Button("대화 이어가기", variant="primary", scale=1)

		# 세션별 질문 기록 표 (페이지 글자도 이 안에 들어 있음)
		history_table = gr.Markdown(table_to_markdown([HISTORY_HEADER]), elem_id="history-table")

		# 페이지 넘기기 (표 밑)
		with gr.Row():
			prev_btn = gr.Button("이전", size="sm")
			next_btn = gr.Button("다음", size="sm")

		# 기록을 닫고 채팅 칸으로 돌아가기
		back_btn = gr.Button("채팅으로 돌아가기", size="sm")

	# 지금 보고 있는 페이지 (전체 행·현재 페이지 번호·기록 주인)
	page_state = gr.State(_empty_page())

	# build_app()에서 쓸 부품 저장
	history_components["column"] = history_col
	history_components["table"] = history_table
	history_components["session_dropdown"] = session_dropdown
	history_components["restore_btn"] = restore_btn
	history_components["prev_btn"] = prev_btn
	history_components["next_btn"] = next_btn
	history_components["back_btn"] = back_btn
	history_components["page_state"] = page_state

	return history_col


@safe_call
def on_restore(session_id: str, state):
	"""
	대화 이어가기 버튼 눌렀을 때

	선택한 session_id로 restore_session()을 호출해 질의응답 탭의 gr.Chatbot에 이전 대화를 채우고,
	state['session_id']를 선택한 세션으로 바꿔 다음 질문이 같은 thread로 이어지게 한다.

	순서
	1. restore_session(session_id, user_id)로 이전 대화를 Chatbot 형식으로 받는다
	   (질의응답 그래프의 대화 기억도 이때 되살아남)
	2. state['session_id']를 고른 대화로 바꾼다
	3. (Chatbot에 넣을 대화, 바뀐 state)를 돌려준다

	- restore_session()은 user_id도 받는다 (3.6.7, 다른 사람의 대화를 불러오지 못하게)

	입력: session_id (드롭다운에서 고른 대화 번호), state (gr.State)
	출력: tuple (list[dict] Chatbot 대화, gr.State)
	"""
	# 1) 이전 대화 불러오기
	user_id = state["user"]["user_id"]
	chat_history = restore_session(session_id, user_id)

	# 2) 다음 질문이 같은 대화로 이어지게 session_id 바꾸기
	state["session_id"] = session_id

	# 3) Chatbot 대화와 state 돌려주기
	return chat_history, state


@safe_call
def on_load_history(state):
	"""
	과거 대화 기록 버튼 눌렀을 때 (질의응답 탭 소스 칸 맨 아래)

	get_history()로 로그인한 사람의 질문 기록을 가져와
	대화(session_id)별로 묶어서 기록 표와 대화 선택 목록을 채운다.

	순서
	1. 로그인 전이면(user가 None) 머리글만 있는 표와 빈 목록을 돌려준다
	2. get_history(user_id)로 기록을 최신순으로 가져온다
	3. session_id별로 마지막 시각·첫 질문·질문 수를 모은다
	   - 기록이 최신순이라 대화에서 처음 만나는 기록이 마지막 시각,
	     마지막에 만나는 기록이 첫 질문이다
	4. [마지막 시각, 첫 질문, 질문 수] 표와 ("시각 · 첫 질문", session_id) 선택지를 만든다

	입력: state (gr.State)
	출력: tuple (기록 표 Markdown, 대화 선택 gr.update, 페이지 상태 dict)
	"""
	table_rows = []

	# 1) 로그인 전이면 빈 표
	if state["user"] == None:
		return table_to_markdown([HISTORY_HEADER]), gr.update(choices=[], value=None), _empty_page()

	# 2) 기록 가져오기 (최신순)
	logs = get_history(state["user"]["user_id"])

	# 3) 대화별로 묶기 (dict는 넣은 순서를 지켜서 최근 대화가 먼저 옴)
	sessions = {}
	for log in logs:
		if log.session_id not in sessions:
			sessions[log.session_id] = {
				"last_time": log.created_at.strftime("%Y-%m-%d %H:%M"),
				"first_question": log.question,
				"count": 0
			}
		sessions[log.session_id]["first_question"] = log.question
		sessions[log.session_id]["count"] = sessions[log.session_id]["count"] + 1

	# 4) 본문 행과 선택지 만들기
	choices = []
	for session_id, info in sessions.items():
		table_rows.append([info["last_time"], info["first_question"], info["count"]])
		choices.append((f"{info['last_time']} · {info['first_question']}", session_id))

	# 5) 첫 페이지 표 만들기
	text, page_state = _make_page(state["user"]["user_id"], table_rows, 1)
	return text, gr.update(choices=choices, value=None), page_state


def _move_page(state, page_state, delta):
	"""
	기록 표를 앞뒤 페이지로 넘기기

	순서
	1. 로그인 전이거나 지금 페이지 상태가 다른 사람 것이면 빈 표를 돌려준다
	   (다른 사람이 로그인하면 표를 지우는데, 그 뒤에도 옛 기록이 남아 나오지 않게 하려고)
	2. 현재 페이지에 delta(-1 또는 +1)를 더해 표를 다시 만든다 (범위 밖이면 _make_page가 맞춘다)

	입력: state (gr.State), page_state (gr.State), delta (-1 이전 / +1 다음)
	출력: tuple (기록 표 Markdown, 바뀐 페이지 상태 dict)
	"""
	if state["user"] == None or page_state["user_id"] != state["user"]["user_id"]:
		return table_to_markdown([HISTORY_HEADER]), _empty_page()

	return _make_page(page_state["user_id"], page_state["rows"], page_state["page"] + delta)


@safe_call
def on_prev_page(state, page_state):
	"""
	이전 페이지 버튼 눌렀을 때 (_move_page에 delta=-1)

	입력: state (gr.State), page_state (gr.State)
	출력: tuple (기록 표 Markdown, 페이지 상태 dict)
	"""
	return _move_page(state, page_state, -1)


@safe_call
def on_next_page(state, page_state):
	"""
	다음 페이지 버튼 눌렀을 때 (_move_page에 delta=+1)

	입력: state (gr.State), page_state (gr.State)
	출력: tuple (기록 표 Markdown, 페이지 상태 dict)
	"""
	return _move_page(state, page_state, 1)