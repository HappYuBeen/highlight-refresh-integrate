"""내 기록 화면 직접 눌러 보는 테스트 (임세준) - 커밋하지 않는 파일

진짜 build_history_tab(), on_restore()로 화면을 띄우고,
DB·그래프가 필요한 restore_session()만 가짜로 바꾼다.
명세서에 기록을 불러오는 시점이 없어서, 테스트에서만 드롭다운에 가짜 대화 2개를 넣어 둔다.

실행 (레포 맨 위 폴더에서):
	poetry run python -m tests.try_history_screen
	→ 브라우저에서 http://127.0.0.1:7860
"""
import sys
import types

import gradio as gr


def make_fake_module(name):
	module = types.ModuleType(name)
	sys.modules[name] = module
	return module


# ----------------------------------------------------------------
# 1. 가짜 restore_session (대화별 가짜 기록)
# ----------------------------------------------------------------
FAKE_CHATS = {
	"s-1": [
		{"role": "user", "content": "기준금리 전망은?"},
		{"role": "assistant", "content": "2.5%로 예상됩니다."},
		{"role": "user", "content": "그 이유는?"},
		{"role": "assistant", "content": "물가가 안정돼서입니다."},
	],
	"s-2": [
		{"role": "user", "content": "환율은 지금 얼마인가요?"},
		{"role": "assistant", "content": "1,390원입니다."},
	],
}


def fake_restore_session(session_id, user_id):
	print(f"[가짜] restore_session({session_id!r}, {user_id!r})")
	if session_id in FAKE_CHATS:
		return FAKE_CHATS[session_id]
	return []


history_service = make_fake_module("hri.service.history_service")
history_service.restore_session = fake_restore_session


# ----------------------------------------------------------------
# 2. table_to_markdown (진짜가 있으면 진짜, 없으면 가짜)
# ----------------------------------------------------------------
try:
	from hri.loader.table_loader import table_to_markdown  # noqa: F401
except ImportError:
	def fake_table_to_markdown(rows):
		lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
		for row in rows[1:]:
			lines.append("| " + " | ".join(row) + " |")
		return "\n".join(lines)

	make_fake_module("hri.loader")
	table_loader = make_fake_module("hri.loader.table_loader")
	table_loader.table_to_markdown = fake_table_to_markdown


# ----------------------------------------------------------------
# 3. 화면 만들기 (app.py와 같은 연결 방식)
# ----------------------------------------------------------------
from hri.view.history_view import build_history_tab, history_components, on_restore  # noqa: E402


def open_for_test():
	"""테스트용: 로그인한 것처럼 내 기록 탭을 보이게 하고 드롭다운에 가짜 대화 2개 넣기"""
	choices = [("2026-10-28 17:19 · 환율은 지금 얼마인가요?", "s-2"), ("2026-10-28 16:25 · 기준금리 전망은?", "s-1")]
	return gr.update(visible=True), gr.update(choices=choices)


with gr.Blocks() as demo:
	state = gr.State({"user": {"user_id": "kim"}, "session_id": "새-대화", "report_ids": [], "last_log_id": None})
	session_view = gr.Markdown()

	with gr.Tabs():
		history_tab = build_history_tab()

		with gr.Tab("질의응답 (가짜)", id="chat"):
			chatbot = gr.Chatbot(label="대화", height=300)

	# app.py와 같은 연결: 대화 이어가기 → 질의응답 탭 Chatbot, state
	history_components["restore_btn"].click(
		fn=on_restore,
		inputs=[history_components["session_dropdown"], state],
		outputs=[chatbot, state]
	).then(
		fn=lambda st: f"지금 state['session_id'] = **{st['session_id']}**",
		inputs=state,
		outputs=session_view
	)

	demo.load(fn=open_for_test, outputs=[history_tab, history_components["session_dropdown"]])

demo.launch(theme=gr.themes.Soft())