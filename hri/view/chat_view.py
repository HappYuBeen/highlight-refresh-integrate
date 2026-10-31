"""질의응답 탭 · build_chat_tab, respond_stream (장유빈) / on_suggest_click, on_term_click, on_feedback (류현수)"""
import logging
import gradio as gr
from hri.dao.qa_log_dao import update_feedback
from hri.domain.schemas import AnswerResult, TermCard
from hri.exception.errors import ExternalApiError, InvalidInputError, OutputFormatError
from hri.service.report_service import owner_filter, search_reports
from hri.service.term_service import MAX_TERM_LENGTH, explain_term
from hri.view.history_view import build_history_panel
from hri.util.logger import safe_call

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# [장유빈] 질의응답 화면 · build_chat_tab, respond_stream
# ---------------------------------------------------------------------
CHAT_TAB_ID = "chat"  # app.py의 질의응답 탭 id (report_view.CHAT_TAB_ID와 같아야 한다)
PERSONA_CHOICES = ["입문자", "실무자", "전문가"]
DEFAULT_PERSONA = "실무자"
DEFAULT_K = 4
MAX_SUGGESTIONS = 3  # 추천 질문 버튼 개수
NEED_LOGIN_MESSAGE = "로그인 후 이용해 주세요."
# 보고서 목록에서 한 번에 고를 수 있는 보고서 수 (CHAT_TITLE_STYLE의 비활성화 선택자도 label.selected 5개 기준)
MAX_SELECTED_REPORTS = 5
SELECT_LIMIT_MESSAGE = "보고서는 최대 {max}개까지 선택할 수 있습니다."

# 소스·채팅 제목 칸의 위아래 스크롤 화살표 숨기기 (head에 넣는 스타일, rem·em만 씀)
CHAT_TITLE_STYLE = """
<style>
#chat-source-col,
#chat-chat-col,
#chat-source-title,
#chat-chat-title {
	overflow: visible;
	scrollbar-width: none;
}
#chat-source-col *,
#chat-chat-col *,
#chat-source-title *,
#chat-chat-title * {
	scrollbar-width: none;
}
#chat-source-col::-webkit-scrollbar,
#chat-chat-col::-webkit-scrollbar,
#chat-source-col *::-webkit-scrollbar,
#chat-chat-col *::-webkit-scrollbar,
#chat-source-title *::-webkit-scrollbar,
#chat-chat-title *::-webkit-scrollbar {
	display: none;
}
/* 소스·채팅 두 칸을 창 높이에 맞춤: 페이지 전체는 스크롤되지 않고, 남는 내용은 각 칸 안에서만 스크롤 */
#chat-source-col,
#chat-chat-col,
#chat-history-col {
	height: calc(100vh - 6rem);
	overflow: hidden;
}
#chat-source-col {
	flex-wrap: nowrap;
}
/* 소스 내용: 남는 높이를 모두 쓰고 넘치면 이 안에서만 스크롤 (Gradio가 넣는 인라인 flex-grow를 덮어씀) */
#chat-source-body {
	flex: 1 1 0 !important;
	min-height: 0;
	overflow-y: auto;
	flex-wrap: nowrap;
}
/* 과거 대화 기록 버튼: 소스 칸 맨 아래에 붙음 */
#chat-history-btn {
	flex: 0 0 auto !important;
}
#chat-history-col {
	overflow-y: auto;
}
/* 보고서 목록: 한 줄에 보고서 하나씩 선택 박스와 함께 */
#chat-report-list [data-testid="checkbox-group"] {
	flex-direction: column;
	flex-wrap: nowrap;
	align-items: stretch;
	gap: 0;
}
/* 보고서 줄 배경: 선택 여부와 상관없이 흰색·연한 회색 번갈아, 글자는 항상 기본 글자색
   (Soft 테마는 background-fill-primary·secondary가 둘 다 neutral-50이라 직접 지정하고,
    고른 줄의 파란 배경·흰 글자(checkbox-label-*-selected)도 여기서 덮어씀) */
#chat-report-list [data-testid="checkbox-group"] > label {
	border-radius: 0;
	background: var(--block-background-fill);
	color: var(--body-text-color);
}
#chat-report-list [data-testid="checkbox-group"] > label:nth-of-type(even) {
	background: var(--neutral-100);
}
.dark #chat-report-list [data-testid="checkbox-group"] > label:nth-of-type(even) {
	background: var(--neutral-700);
}
/* 보고서를 최대 개수(MAX_SELECTED_REPORTS, 5개)만큼 고르면 나머지 보고서는 흐리게 하고 누를 수 없게 함
   (Gradio가 고른 보고서 줄에 selected 클래스를 붙인다) */
#chat-report-list [data-testid="checkbox-group"]:has(> label.selected ~ label.selected ~ label.selected ~ label.selected ~ label.selected) > label:not(.selected) {
	opacity: 0.45;
	pointer-events: none;
}
#chat-bot {
	flex: 1 1 auto;
	min-height: 0;
	height: auto;
}
#chat-bot .bubble-wrap {
	overflow-y: auto;
}
</style>
"""
ERROR_MESSAGE = "답변을 만드는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
# 첫 글자 조각이 오기 전(질문 다듬기·검색 중)에 답변 칸에 보여 줄 문구. 빈 답변 칸만 떠 있으면 멈춘 것처럼 보여서 넣는다
THINKING_MESSAGE = "⏳ 답변을 준비하는 중..."

# app.py·다른 담당자가 컴포넌트를 꺼내 쓸 때 쓰는 모음 (login_components 등과 같은 방식)
chat_components = {}


def _ask_stream(**kwargs):
	"""qa_service.ask_stream을 호출하는 시점에 불러와 실행한다.

	qa_service는 그래프(.env 설정 필요)를 불러오는 모듈이라, 화면 파일을 불러올 때가 아니라
	실제로 질문할 때 가져온다 (화면 테스트가 .env 없이도 돌아가고, 테스트에서 이 함수만 바꿔 끼울 수 있다).
	"""
	from hri.service.qa_service import ask_stream

	return ask_stream(**kwargs)


def _user_id(state: dict) -> str | None:
	"""state['user']에서 로그인한 사용자의 아이디를 꺼낸다. 로그인 전이면 None.

	user가 dict여도, user_id 속성을 가진 객체(User)여도 읽을 수 있다.
	"""
	user = (state or {}).get("user")
	if user is None:
		return None
	if isinstance(user, dict):
		return user.get("user_id")
	return getattr(user, "user_id", None)


def _sources_to_markdown(sources: list[dict]) -> str:
	"""format_sources() 결과를 화면에 보여 줄 Markdown으로 바꾼다.

	한 줄에 '보고서명 · p.쪽수 · 섹션명(차트는 그림 n(차트))'이다. 섹션명이 없는 표는 '표'로 적는다.
	출처가 없으면 빈 문자열을 돌려준다 ('자료에 없습니다' 답변에는 출처를 보이지 않는다).
	"""
	if not sources:
		return ""
	lines = ["**출처**"]
	for source in sources:
		start, end = source["page_start"], source["page_end"]
		pages = f"p.{start}" if start == end else f"p.{start}-{end}"
		label = source["label"] or ("표" if source["kind"] == "table" else "")
		lines.append("- " + " · ".join(part for part in (source["report_title"], pages, label) if part))
	return "\n".join(lines)


def _suggest_updates(suggestions: list[str]) -> list:
	"""추천 질문 버튼 MAX_SUGGESTIONS개의 갱신값을 만든다. 질문이 있는 버튼만 보이고 나머지는 숨긴다."""
	updates = []
	for i in range(MAX_SUGGESTIONS):
		if i < len(suggestions):
			updates.append(gr.update(value=suggestions[i], visible=True))
		else:
			updates.append(gr.update(value="", visible=False))
	return updates



# ---------------------------------------------------------------------
# [류현수] 추천 질문 · build_suggest_buttons, on_suggest_click
# ---------------------------------------------------------------------
# 대화를 처음 시작할 때 보여 줄 질문 (임시. 서비스가 완성되면 기능을 잘 보여 주는 질문으로 바꾼다)
STARTER_QUESTIONS = [
	"이 보고서의 핵심 내용을 요약해 줘",
	"보고서에서 전망한 경제성장률은 얼마야?",
	"물가와 금리 전망은 어떻게 돼?",
]


def build_suggest_buttons(message: gr.Textbox) -> list[gr.Button]:
	"""추천 질문 버튼 MAX_SUGGESTIONS개를 만든다. (build_chat_tab 안에서 질문 입력칸 다음에 호출)

	처음에는 STARTER_QUESTIONS를 보여 주고, 버튼을 누르면 on_suggest_click으로 질문 입력칸(message)에 그 질문을 넣는다.
	답변이 끝난 뒤 respond_stream()이 버튼 글자를 후속 질문으로 바꿔도 같은 클릭 연결을 그대로 쓴다
	(state가 필요 없어서 연결도 여기서 한다).

	반환: 버튼 목록 (chat_components['suggest_buttons'])
	"""
	buttons = []
	for i in range(MAX_SUGGESTIONS):
		question = STARTER_QUESTIONS[i] if i < len(STARTER_QUESTIONS) else ""
		button = gr.Button(value=question, visible=bool(question), size="sm")
		# 버튼을 입력으로 넘기면 버튼에 적힌 글자(질문)가 on_suggest_click에 들어온다
		button.click(on_suggest_click, inputs=button, outputs=message)
		buttons.append(button)
	return buttons


def on_suggest_click(question: str) -> str:
	"""추천 질문 버튼: 누른 질문을 질문 입력칸에 넣는다. (바로 보내지 않고, 사용자가 고쳐서 보낼 수 있게 한다)"""
	return (question or "").strip()

def build_chat_tab():
	"""질의응답 탭 화면을 만든다. (gr.Blocks의 gr.Tabs 안에서 호출)

	화면을 소스(3)·채팅(6) 두 칸으로 나누고, 스튜디오는 오른쪽에서 펼쳐지는 사이드바로 둔다. (UI 레퍼런스: 소스·채팅·스튜디오 3단 배치)
	- 왼쪽(소스): 보고서 목록(선택 박스, 최대 5개)·페르소나(입문자/실무자/전문가)·검색 옵션(k, MMR),
	  답변이 끝나면 그 아래에 출처가 나온다. 칸 맨 아래에는 '과거 대화 기록' 버튼이 붙어 있고, 누르면
	  채팅 칸 자리에 과거 대화 기록(build_history_panel, 임세준)이 보인다 (연결은 build_app).
	- 가운데(채팅): 추천 질문 버튼 3개(한 줄) → 대화창(gr.Chatbot) → 👍/👎 → 질문 입력칸·전송 버튼.
	  질문 입력칸은 추천 질문 버튼보다 먼저 만들어야 해서 render=False로 만들고, 화면 맨 아래에서 render()로 놓는다.
	- 오른쪽(스튜디오): gr.Sidebar(position="right")로 만들어 처음에는 접어 두고, 화면 오른쪽 끝의
	  버튼(Gradio 기본 제공)을 누르면 펼치거나 접는다. 안의 '용어 카드' 메뉴(gr.Accordion) 검색 창은
	  build_term_panel(류현수)이 만든다.
	추천 질문 버튼은 처음에 시작 질문 3개를 보여 주고(build_suggest_buttons, 류현수), 답변이 끝난 뒤
	respond_stream()이 후속 질문으로 바꾼다. 버튼을 누르면 질문 입력칸에 그 질문이 들어간다(on_suggest_click).
	👍/👎 버튼은 숨겨 두고, 기록이 남은 답변이 끝나면 보인다 (connect_feedback_events·on_feedback, 류현수).
	탭은 사용자 탭 줄(gr.Tabs) 안에 보이게 만들고, 로그인 전에는 build_app()이 탭 줄을 통째로 숨긴다.
	이벤트 연결은 gr.State가 필요해서 build_app()이 connect_chat_events(state)로 한다.

	반환: gr.Tab (컴포넌트는 chat_components로 꺼내 쓴다)
	"""
	with gr.Tab("질의응답", id=CHAT_TAB_ID) as tab:
		with gr.Row(equal_height=False):
			# 왼쪽: 소스 (보고서 선택·페르소나·검색 옵션, 답변 뒤에는 출처)
			# 소스 내용은 위쪽에서 남는 높이를 쓰며 넘치면 그 안에서 스크롤, '과거 대화 기록' 버튼은 칸 맨 아래에 붙는다
			with gr.Column(scale=3, variant="panel", elem_id="chat-source-col"):
				with gr.Column(elem_id="chat-source-body"):
					gr.Markdown("#### 소스", elem_id="chat-source-title")
					# 보고서 목록(선택 박스). 키 이름은 다른 화면·app.py와 맞추려고 report_dropdown 그대로 둔다 (선택지·값 갱신 방식은 Dropdown과 같다)
					# 최대 MAX_SELECTED_REPORTS개까지 고를 수 있어서 전체 선택 박스는 두지 않는다
					report_dropdown = gr.CheckboxGroup(
						label=f"보고서 선택 (최대 {MAX_SELECTED_REPORTS}개)", choices=[], elem_id="chat-report-list"
					)
					verify_btn = gr.Button("🔍 수치 검증 (ECOS)", size="sm")
					persona = gr.Radio(PERSONA_CHOICES, value=DEFAULT_PERSONA, label="페르소나")
					with gr.Accordion("검색 옵션", open=False):
						k = gr.Slider(1, 10, value=DEFAULT_K, step=1, label="가져올 근거 수 (k)")
						use_mmr = gr.Checkbox(label="MMR 사용 (서로 다른 근거 고르기)", value=False)
					# 출처: 답변이 끝나면 respond_stream()이 채운다 (없으면 빈칸)
					sources = gr.Markdown()
				# 누르면 채팅 칸 자리에 과거 대화 기록(build_history_panel, 임세준)이 보인다 (연결은 build_app)
				history_btn = gr.Button("과거 대화 기록", elem_id="chat-history-btn")
			# 가운데: 채팅 (추천 질문 → 대화창 → 질문 입력칸)
			with gr.Column(scale=6, variant="panel", elem_id="chat-chat-col") as chat_col:
				gr.Markdown("#### 채팅", elem_id="chat-chat-title")
				# 추천 질문: 처음에는 시작 질문(STARTER_QUESTIONS, 류현수)을 보여 주고, 답변이 끝나면 후속 질문으로 바꾼다
				message = gr.Textbox(
					show_label=False,
					placeholder="선택한 보고서에 대해 궁금한 점을 물어보세요",
					lines=1,
					scale=1,
					container=False,
					render=False,
				)
				with gr.Row():
					suggest_buttons = build_suggest_buttons(message)
				# 높이는 CHAT_TITLE_STYLE에서 창 크기에 맞춘다 (대화창 안에서만 스크롤)
				chatbot = gr.Chatbot(show_label=False, elem_id="chat-bot")
				# 피드백: 처음에는 숨기고, 기록(log_id)이 남은 답변이 끝나면 보인다 (connect_feedback_events, 류현수)
				with gr.Row():
					like_btn = gr.Button("👍", visible=False, scale=0, min_width=60, size="sm")
					dislike_btn = gr.Button("👎", visible=False, scale=0, min_width=60, size="sm")
				with gr.Row(equal_height=True):
					message.render()
					send_btn = gr.Button("전송", variant="primary", scale=0, min_width=90)
			# 가운데(채팅 칸 자리): 과거 대화 기록. 처음에는 숨김
			build_history_panel()
		# 오른쪽: 스튜디오 (용어 카드). gr.Sidebar라서 화면 오른쪽 끝의 버튼으로 펼치고 접는다 (처음에는 접힘)
		with gr.Sidebar(label="용어 검색", position="right", open=False) as studio:
			gr.Markdown("#### 용어검색")
			with gr.Accordion("용어 카드", open=True) as term_menu:
				# 용어 검색 창 (류현수). 페르소나를 같이 써서 질문 답변과 같은 난이도로 설명한다
				build_term_panel(persona)

	chat_components.update(
		tab=tab,
		chat_col=chat_col,
		history_btn=history_btn,
		chatbot=chatbot,
		message=message,
		send_btn=send_btn,
		sources=sources,
		suggest_buttons=suggest_buttons,
		like_btn=like_btn,
		dislike_btn=dislike_btn,
		persona=persona,
		report_dropdown=report_dropdown,
		k=k,
		use_mmr=use_mmr,
		term_menu=term_menu,
		studio=studio,
		verify_btn=verify_btn,
	)
	return tab


def refresh_report_choices(state: dict, selected: list[int] | None):
	"""보고서 드롭다운의 선택지를 DB의 보고서 목록으로 채운다. (질의응답 탭을 열 때 실행)

	선택지는 (제목, report_id) 쌍이다. 로그인한 사용자 기준으로 일반 사용자는 본인이 올린 보고서만,
	관리자는 전체를 보여 준다. 이미 고른 보고서가 있으면 그대로 두고,
	없으면 보고서 목록 화면에서 고른 state['report_ids']를 고른 상태로 둔다. 목록에 없는 번호는 뺀다.
	"""
	reports = search_reports("", user_id=owner_filter((state or {}).get("user")))
	choices = [(r.title, r.report_id) for r in reports]
	existing = {r.report_id for r in reports}
	wanted = selected or (state or {}).get("report_ids") or []
	return gr.update(choices=choices, value=[i for i in wanted if i in existing])


def on_limit_reports(report_ids: list[int]):
	"""보고서 목록 값이 바뀔 때: MAX_SELECTED_REPORTS개를 넘으면 앞에서부터 그 개수만 남기고 안내한다.

	화면에서는 5개를 고르면 나머지가 비활성화되지만(CHAT_TITLE_STYLE), 키보드로 고르거나
	보고서 목록 화면에서 더 많이 골라 넘어온 경우를 위해 서버에서도 한 번 더 맞춘다.

	입력: report_ids (선택한 보고서 번호 목록)
	출력: 보고서 목록 갱신 (넘지 않으면 그대로)
	"""
	report_ids = list(report_ids or [])
	if len(report_ids) <= MAX_SELECTED_REPORTS:
		return gr.update()

	gr.Info(SELECT_LIMIT_MESSAGE.format(max=MAX_SELECTED_REPORTS))
	return gr.update(value=report_ids[:MAX_SELECTED_REPORTS])


def respond_stream(message: str, history: list, persona: str, report_ids: list[int], k: int, use_mmr: bool, state: dict):
	"""질문을 ask_stream()에 보내고, 글자 조각이 올 때마다 대화창을 갱신하며 답변이 흘러나오게 한다.

	처음에 입력칸을 비우고 이전 출처·추천 질문을 지우며, 답변 칸에 THINKING_MESSAGE를 띄운다.
	글자 조각(str)이 오면 그 문구를 지우고 마지막 답변에 이어 붙이고,
	마지막에 AnswerResult가 오면 출처(Markdown)·추천 질문 버튼·state['last_log_id']를 갱신한다.
	로그인 전이면 안내만 하고, 질문 처리 중 예외가 나면 오류 안내 문구를 답변으로 보여 준다.

	반환(yield): (대화 기록, 질문 입력칸, 출처, 추천 질문 버튼 3개, state) — build_chat_tab 컴포넌트 순서와 같다.
	"""
	state = state or {}
	history = list(history or [])
	history.append({"role": "user", "content": message})
	history.append({"role": "assistant", "content": ""})
	keep = gr.update()  # 값을 바꾸지 않는 갱신

	user_id = _user_id(state)
	if user_id is None:
		history[-1]["content"] = NEED_LOGIN_MESSAGE
		yield history, keep, keep, *[keep] * MAX_SUGGESTIONS, state
		return

	# 새 질문이 시작되면 입력칸을 비우고 이전 질문의 출처·추천 질문을 지운다.
	# 답변 칸에는 첫 글자 조각이 올 때까지 준비 중 문구를 보여 준다 (질문 다듬기·검색이 끝나야 글자가 나오기 시작한다)
	history[-1]["content"] = THINKING_MESSAGE
	yield history, "", "", *_suggest_updates([]), state

	answer = ""
	try:
		stream = _ask_stream(
			question=message,
			persona=persona,
			session_id=state.get("session_id"),
			user_id=user_id,
			report_ids=list(report_ids) if report_ids else None,
			k=int(k),
			use_mmr=bool(use_mmr),
		)
		for chunk in stream:
			if isinstance(chunk, AnswerResult):
				# 마지막 결과: 최종 답변·출처·추천 질문·기록 번호(피드백용)를 반영한다
				history[-1]["content"] = chunk.answer or answer
				new_state = {**state, "last_log_id": chunk.log_id}
				yield history, keep, _sources_to_markdown(chunk.sources), *_suggest_updates(chunk.suggestions), new_state
				return
			answer += chunk
			history[-1]["content"] = answer
			yield history, keep, keep, *[keep] * MAX_SUGGESTIONS, state
	except Exception:
		logger.exception("질의응답 처리 중 오류")
		history[-1]["content"] = ERROR_MESSAGE
		yield history, keep, keep, *[keep] * MAX_SUGGESTIONS, state


def connect_chat_events(state: gr.State) -> None:
	"""질의응답 탭의 이벤트를 연결한다. (build_app()의 gr.Blocks 안에서 build_chat_tab() 뒤에 호출)

	- 질문 입력칸에서 Enter, 전송 버튼 클릭 → respond_stream (답변이 흘러나옴)
	- 질의응답 탭을 열 때 → refresh_report_choices (보고서 선택지 채우기)
	- 보고서 목록 값이 바뀔 때 → on_limit_reports (최대 5개까지만 남기기)
	  (connect_feedback_events가 앞뒤로 👍/👎 숨기기·보이기를 붙여 차례로 실행한다, 류현수)
	"""
	c = chat_components
	stream_inputs = [c["message"], c["chatbot"], c["persona"], c["report_dropdown"], c["k"], c["use_mmr"], state]
	stream_outputs = [c["chatbot"], c["message"], c["sources"], *c["suggest_buttons"], state]
	stream = {"fn": respond_stream, "inputs": stream_inputs, "outputs": stream_outputs}
	# 질문 보내기: 👍/👎 숨기기 → respond_stream → 기록이 새로 생긴 답변이면 👍/👎 보이기 (류현수)
	connect_feedback_events(state, [c["message"].submit, c["send_btn"].click], stream)
	c["tab"].select(refresh_report_choices, inputs=[state, c["report_dropdown"]], outputs=c["report_dropdown"])
	# 보고서 목록 값이 바뀌면 최대 개수(5개)를 넘지 않게 맞추기 (탭 열기·질의응답으로 이동·업로드로 바뀔 때도 포함)
	c["report_dropdown"].change(on_limit_reports, inputs=c["report_dropdown"], outputs=c["report_dropdown"])
	c["verify_btn"].click(
		fn=on_verify,
		inputs=[c["chatbot"], c["report_dropdown"], c["message"], state],
		outputs=[c["chatbot"], c["message"]],
	)
# ---------------------------------------------------------------------
# [김희진] 수치 검증 · on_verify (verify_and_expand를 질의응답 탭에 연결)
# ---------------------------------------------------------------------
VERIFY_MAX_REPORTS = 3 # 한 번에 검증하는 보고서 수 (보고서마다 ECOS 조회와 LLM 호출이 여러 번 나간다)
VERIFY_DEFAULT_QUESTION = "선택한 보고서의 수치를 ECOS 통계로 검증해 줘"
VERIFY_NEED_REPORT_MESSAGE = "검증할 보고서를 선택하세요."
VERIFY_TOO_MANY_NOTE = "⚠️ 한 번에 {max}개까지 검증합니다. 앞의 {max}개만 검증했어요."
VERIFY_FAIL_MESSAGE = "검증 결과를 만들지 못했습니다."
VERIFY_API_ERROR_MESSAGE = "ECOS 통계를 불러오지 못해 검증하지 못했습니다. 잠시 후 다시 시도해 주세요."
VERIFY_ERROR_MESSAGE = "검증 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."

def _verify_and_expand(report_id: int, request: str):
	"""verify_and_expand를 호출하는 시점에 불러와 실행한다 (이유는 _ask_stream과 같다)."""
	from hri.prompt.summary_prompt import verify_and_expand

	return verify_and_expand(report_id, request)

def _escape_tilde(text: str) -> str:
	"""Markdown에서 ~가 취소선으로 해석되지 않게 \\~로 바꾼다 (예: '2~3%')"""
	return (text or "").replace("~", "\\~")

def _verify_markdown(title: str, result) -> str:
	"""보고서 1건의 검증 결과(VerifyResult)를 일치·차이·보완·검증하지 못한 항목 Markdown으로 바꾼다."""
	lines = [f"### 🔍 {_escape_tilde(title)} · 수치 검증"]
	sections = [
		("✅ 일치·근사", result.matches),
		("⚠️ 차이", result.differences),
		("➕ ECOS로 보완", result.additions),
		("❔ 검증하지 못한 항목", result.unchecked),
	]
	for label, items in sections:
		if items:
			lines += ["", f"**{label}**"]
			lines += [f"- {_escape_tilde(item)}" for item in items]
			lines += ["", f"*확인 시간: {result.checked_at:%Y-%m-%d %H:%M}*"]

	return "\n".join(lines)

@safe_call
def on_verify(history: list, report_ids: list[int] | None, message: str, state: dict):
	"""수치 검증 버튼: 선택한 보고서마다 verify_and_expand()를 실행해 결과를 대화창에 답변으로 보여 준다. (제너레이터)
	
	순서
	1. 질문 입력칸의 글(없으면 기본 문구)을 사용자 말풍선으로 넣는다
	2. 로그인 전이거나 보고서를 고르지 않았으면 안내만 보여 준다
	3. 보고서를 VERIFY_MAX_REPORTS개까지 하나씩 검증하며 '(n/전체)' 진행 상황을 보여 준다
		- 한 보고서가 실패해도(ECOS 오류·형식 오류 등) 그 보고서 자리에만 안내 문구를 넣고 나머지는 계속한다
	4. 질문 입력칸은 비운다. qa_logs에는 기록하지 않는다 (route 종류가 정해져 있어서 👍/👎도 이전 답변 기준 그대로)

	yield 값: (대화 기록, 질문 입력칸)
	"""
	history = list(history or [])
	keep = gr.update()
	request = (message or "").strip()
	history.append({"role": "user", "content": request or VERIFY_DEFAULT_QUESTION})
	user = (state or {}).get("user")
	ids = list(dict.fromkeys(report_ids or []))

	if user is None:
		history.append({"role": "assistant", "content": NEED_LOGIN_MESSAGE})
		yield history, keep
		return
	if not ids:
		history.append({"role": "assistant", "content": VERIFY_NEED_REPORT_MESSAGE})
		yield history, keep
		return
	notes = []

	if len(ids) > VERIFY_MAX_REPORTS:
		ids = ids[:VERIFY_MAX_REPORTS]
		notes.append(VERIFY_TOO_MANY_NOTE.format(max=VERIFY_MAX_REPORTS))
	try:
		titles = {r.report_id: r.title for r in search_reports("", user_id=owner_filter(user))}
	except Exception:
		logger.exception("보고서 제목 조회 실패")
		titles = {}
	history.append({"role": "assistant", "content": ""})
	parts = list(notes)

	for no, report_id in enumerate(ids, 1):
		name = titles.get(report_id, f"보고서 {report_id}")
		history[-1]["content"] = "\n\n---\n\n".join([*parts, f"⏳ ECOS 통계로 검증 중({no}/{len(ids)}): {name}"])
		yield history, ""
		try:
			result = _verify_and_expand(report_id, request)
		except ExternalApiError:
			logger.exception("ECOS 검증 실패: report_id=%s", report_id)
			parts.append(f"### 🔍 {_escape_tilde(name)}\n{VERIFY_API_ERROR_MESSAGE}")
		except OutputFormatError:
			parts.append(f"### 🔍 {_escape_tilde(name)}\n{VERIFY_FAIL_MESSAGE}")
		except Exception:
			logger.exception("수치 검증 실패: report_id=%s", report_id)
			parts.append(f"### 🔍 {_escape_tilde(name)}\n{VERIFY_ERROR_MESSAGE}")
		else:
			parts.append(_verify_markdown(name, result))
	history[-1]["content"] = "\n\n---\n\n".join(parts)
	yield history, ""

# ---------------------------------------------------------------------
# [류현수] 피드백 · connect_feedback_events, on_feedback
# ---------------------------------------------------------------------
FEEDBACK_SAVED_MESSAGE = {1: "👍 피드백을 남겼습니다.", -1: "👎 피드백을 남겼습니다."}
FEEDBACK_NO_ANSWER_MESSAGE = "피드백을 남길 답변이 없습니다."
FEEDBACK_ERROR_MESSAGE = "피드백을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요."


def _hide_feedback(state: dict):
	"""새 질문을 보낼 때: 👍/👎 버튼을 숨기고, 질문 전의 기록 번호를 기억해 둔다.

	반환: (👍 갱신, 👎 갱신, 질문 전 last_log_id)
	"""
	hidden = gr.update(visible=False, variant="secondary")
	return hidden, hidden, (state or {}).get("last_log_id")


def _show_feedback(state: dict, before_log_id: int | None):
	"""답변이 끝났을 때: 이번 답변의 기록이 새로 생겼으면 👍/👎 버튼을 보인다.

	로그인 전 안내·오류 답변처럼 기록이 새로 생기지 않으면(last_log_id가 질문 전과 같거나 없음) 숨긴 채로 둔다
	(그대로 보이면 이전 답변에 피드백이 저장되므로).
	"""
	log_id = (state or {}).get("last_log_id")
	visible = log_id is not None and log_id != before_log_id
	shown = gr.update(visible=visible, variant="secondary")
	return shown, shown


def connect_feedback_events(state: gr.State, triggers: list, stream: dict) -> None:
	"""질문 보내기와 👍/👎 버튼을 연결한다. (connect_chat_events 안에서 호출)

	triggers(입력칸 Enter·전송 버튼)마다 아래를 차례로(.then) 실행한다. 따로 연결하면 실행 순서가 보장되지 않아
	숨기기가 답변 뒤에 실행될 수 있으므로 한 줄로 잇는다.
	1) _hide_feedback: 버튼 숨기기, 질문 전 기록 번호 기억
	2) stream: respond_stream (connect_chat_events가 넘긴 fn·inputs·outputs 그대로)
	3) _show_feedback: 기록이 새로 생긴 답변이면 버튼 보이기
	그리고 👍 클릭 → on_feedback(1, state), 👎 클릭 → on_feedback(-1, state)
	"""
	c = chat_components
	buttons = [c["like_btn"], c["dislike_btn"]]
	# 질문 전 기록 번호: 오류·로그인 안내처럼 새 기록이 없는 답변에 버튼을 보이지 않으려고 기억해 둔다
	before_log_id = gr.State(None)
	for trigger in triggers:
		(
			trigger(_hide_feedback, inputs=state, outputs=[*buttons, before_log_id])
			.then(**stream)
			.then(_show_feedback, inputs=[state, before_log_id], outputs=buttons)
		)
	c["like_btn"].click(lambda st: on_feedback(1, st), inputs=state, outputs=buttons)
	c["dislike_btn"].click(lambda st: on_feedback(-1, st), inputs=state, outputs=buttons)


def on_feedback(feedback: int, state: dict):
	"""👍/👎 버튼: state['last_log_id']의 질문 기록에 피드백(1: 👍, -1: 👎)을 저장한다.

	- 저장하면 누른 버튼을 강조(primary)하고 안내 알림을 띄운다. 다시 누르면 새 값으로 바뀐다
	- 기록 번호가 없거나 저장에 실패하면 경고 알림만 띄우고 버튼은 그대로 둔다

	반환: (👍 갱신, 👎 갱신)
	"""
	keep = gr.update()
	log_id = (state or {}).get("last_log_id")
	if log_id is None:
		gr.Warning(FEEDBACK_NO_ANSWER_MESSAGE)
		return keep, keep
	try:
		saved = update_feedback(log_id, feedback)
	except Exception:
		logger.exception("피드백 저장 실패: log_id=%s", log_id)
		saved = False
	if not saved:
		gr.Warning(FEEDBACK_ERROR_MESSAGE)
		return keep, keep
	gr.Info(FEEDBACK_SAVED_MESSAGE[feedback])
	return (
		gr.update(variant="primary" if feedback == 1 else "secondary"),
		gr.update(variant="primary" if feedback == -1 else "secondary"),
	)

# ---------------------------------------------------------------------
# [류현수] 용어 검색 · build_term_panel, on_term_click
# ---------------------------------------------------------------------
TERM_NOT_FOUND_MESSAGE = "ECOS 통계용어 사전에서 '{term}'을(를) 찾지 못했습니다. 다른 표현(예: 약어·정식 명칭)으로 검색해 보세요."
TERM_API_ERROR_MESSAGE = "ECOS 통계용어를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요."
TERM_FAILED_MESSAGE = "용어 설명을 만들지 못했습니다. 잠시 후 다시 시도해 주세요."

# 용어 검색 창 컴포넌트 모음 (chat_components와 같은 방식). term_input, term_btn, term_result
term_components = {}


def build_term_panel(persona: gr.Radio):
	"""용어 검색 창을 만든다. (build_chat_tab의 '용어 검색' 메뉴 안에서 호출)

	용어 입력칸·'풀이 보기' 버튼·결과(Markdown)를 두고, 버튼 클릭과 Enter를 on_term_click에 연결한다.
	state가 필요 없어서 이벤트 연결도 여기서 한다 (connect_chat_events에 넣지 않는다).

	입력: persona — 질의응답 탭의 페르소나 gr.Radio. 질문 답변과 같은 난이도로 용어를 설명한다
	반환: 없음 (컴포넌트는 term_components로 꺼내 쓴다)
	"""
	term_input = gr.Textbox(
		label="용어",
		placeholder="예: GDP, 경상수지, 기준금리",
		max_length=MAX_TERM_LENGTH,
		lines=1,
	)
	term_btn = gr.Button("풀이 보기", variant="primary")
	term_result = gr.Markdown()

	inputs = [term_input, persona]
	term_btn.click(on_term_click, inputs=inputs, outputs=term_result)
	term_input.submit(on_term_click, inputs=inputs, outputs=term_result)
	term_components.update(term_input=term_input, term_btn=term_btn, term_result=term_result)


def _term_card_to_markdown(card: TermCard) -> str:
	"""TermCard를 화면에 보여 줄 Markdown으로 바꾼다. (쉬운 설명 먼저, 원문 정의는 인용으로)"""
	definition = "\n> ".join(card.definition.splitlines())
	return (
		f"#### {card.term}\n\n"
		f"{card.explanation}\n\n"
		f"**원문 정의** ({card.source})\n\n"
		f"> {definition}"
	)


def on_term_click(term: str, persona: str | None = DEFAULT_PERSONA) -> str:
	"""'풀이 보기' 버튼: explain_term()으로 용어 풀이를 만들어 결과 Markdown을 돌려준다.

	- 찾으면 쉬운 설명 + 원문 정의(출처)
	- ECOS 통계용어에 없으면 다른 표현으로 검색해 보라는 안내
	- 입력 오류·ECOS 오류·LLM 오류는 안내 문구로 보여 준다 (자세한 API 오류 내용은 보이지 않고 로그에만 남긴다)
	"""
	try:
		card = explain_term(term, persona or DEFAULT_PERSONA)
	except InvalidInputError as e:
		return f"⚠️ {e}"
	except ExternalApiError:
		logger.exception("용어 풀이 중 ECOS 오류")
		return f"⚠️ {TERM_API_ERROR_MESSAGE}"
	except OutputFormatError:
		logger.exception("용어 풀이 중 LLM 출력 오류")
		return f"⚠️ {TERM_FAILED_MESSAGE}"
	if card is None:
		return f"ℹ️ {TERM_NOT_FOUND_MESSAGE.format(term=(term or '').strip())}"
	return _term_card_to_markdown(card)