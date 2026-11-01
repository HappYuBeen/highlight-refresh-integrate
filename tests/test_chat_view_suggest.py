"""질의응답 탭의 추천 질문(chat_view.build_suggest_buttons·on_suggest_click) 테스트 (류현수)"""

import gradio as gr

from hri.view import chat_view
from hri.view.chat_view import MAX_SUGGESTIONS, STARTER_QUESTIONS, on_suggest_click


# [확인 1] 시작 질문은 버튼 개수만큼 있고, 빈 질문이 없다
def test_starter_questions():
	assert len(STARTER_QUESTIONS) == MAX_SUGGESTIONS
	assert all(q.strip() for q in STARTER_QUESTIONS)


# [확인 2] 질의응답 탭을 처음 만들면 추천 질문 버튼에 시작 질문이 보인다
def test_buttons_show_starter_questions():
	with gr.Blocks():
		chat_view.build_chat_tab()
	buttons = chat_view.chat_components["suggest_buttons"]
	assert [b.value for b in buttons] == STARTER_QUESTIONS
	assert all(b.visible for b in buttons)


# [확인 3] 버튼을 누르면 그 질문이 질문 입력칸에 들어간다 (버튼 → 입력칸 연결)
def test_buttons_fill_message_box():
	with gr.Blocks() as demo:
		chat_view.build_chat_tab()
	components = chat_view.chat_components
	message_id = components["message"]._id
	for button in components["suggest_buttons"]:
		clicks = [fn for fn in demo.fns.values() if (button._id, "click") in fn.targets]
		assert len(clicks) == 1
		assert clicks[0].fn is on_suggest_click
		assert [c._id for c in clicks[0].inputs] == [button._id]
		assert [c._id for c in clicks[0].outputs] == [message_id]


# [확인 4] 누른 질문을 앞뒤 공백 없이 돌려준다 (빈 버튼이면 빈 문자열)
def test_on_suggest_click():
	assert on_suggest_click("  물가 전망은?  ") == "물가 전망은?"
	assert on_suggest_click("") == ""
	assert on_suggest_click(None) == ""


# [확인 5] 시작 질문이 버튼 수보다 적으면 남는 버튼은 숨긴다
def test_fewer_starters_hide_extra_buttons(monkeypatch):
	monkeypatch.setattr(chat_view, "STARTER_QUESTIONS", ["질문 하나"])
	with gr.Blocks():
		buttons = chat_view.build_suggest_buttons(gr.Textbox())
	assert [(b.value, b.visible) for b in buttons] == [("질문 하나", True), ("", False), ("", False)]
