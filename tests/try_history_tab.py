"""내 기록 화면 테스트 (임세준) - 커밋하지 않는 파일

진짜 build_history_tab(), on_restore()는 그대로 쓰고,
DB와 그래프가 필요한 restore_session()만 가짜로 바꿔서 확인한다.
(table_to_markdown이 아직 develop에 없으면 그것도 가짜로 바꿈)

실행 (레포 맨 위 폴더에서):
	poetry run python -m tests.try_history_tab
"""
import sys
import types

import gradio as gr


def make_fake_module(name):
	module = types.ModuleType(name)
	sys.modules[name] = module
	return module


# ----------------------------------------------------------------
# 1. 가짜 restore_session (DB·그래프 대신)
# ----------------------------------------------------------------
restore_calls = []


def fake_restore_session(session_id, user_id):
	restore_calls.append((session_id, user_id))
	return [
		{"role": "user", "content": "기준금리 전망은?"},
		{"role": "assistant", "content": "2.5%로 예상됩니다."},
	]


history_service = make_fake_module("hri.service.history_service")
history_service.restore_session = fake_restore_session


# ----------------------------------------------------------------
# 2. table_to_markdown (진짜가 있으면 진짜, 없으면 가짜)
# ----------------------------------------------------------------
try:
	from hri.loader.table_loader import table_to_markdown  # noqa: F401
	print("table_to_markdown: 진짜 함수 사용")
except ImportError:
	def fake_table_to_markdown(rows):
		lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
		for row in rows[1:]:
			lines.append("| " + " | ".join(row) + " |")
		return "\n".join(lines)

	make_fake_module("hri.loader")
	table_loader = make_fake_module("hri.loader.table_loader")
	table_loader.table_to_markdown = fake_table_to_markdown
	print("table_to_markdown: 아직 없어서 가짜 함수 사용")


# ----------------------------------------------------------------
# 3. 테스트
# ----------------------------------------------------------------
from hri.view import history_view  # noqa: E402
from hri.view.history_view import build_history_tab, history_components, on_restore  # noqa: E402

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


# build_history_tab
with gr.Blocks() as demo:
	with gr.Tabs():
		tab = build_history_tab()

check("1 build_history_tab → gr.Tab", isinstance(tab, gr.Tab), type(tab).__name__)
check("2 탭 id='history', 처음엔 숨김", tab.id == "history" and tab.visible == False)
check("3 부품 3개 저장 (표·드롭다운·버튼)", list(history_components.keys()) == ["table", "session_dropdown", "restore_btn"], list(history_components.keys()))
check("4 표는 Markdown, 머리글 있음", isinstance(history_components["table"], gr.Markdown) and "마지막 시각" in history_components["table"].value)
print("     표 내용:", repr(history_components["table"].value))

# on_restore
state = {"user": {"user_id": "kim"}, "session_id": "새-대화", "report_ids": [], "last_log_id": None}
chat, new_state = on_restore("s-1", state)

check("5 on_restore → Chatbot 대화(list[dict])", chat == [{"role": "user", "content": "기준금리 전망은?"}, {"role": "assistant", "content": "2.5%로 예상됩니다."}])
check("6 state['session_id'] → 's-1'", new_state["session_id"] == "s-1", new_state["session_id"])
check("7 restore_session(session_id, user_id)로 부름", restore_calls[-1] == ("s-1", "kim"), restore_calls[-1])
check("8 명세서 함수만 있음", hasattr(history_view, "on_load_history") == False and hasattr(history_view, "show_chat_tab") == False)

print(f"\n통과 {passed}/{total}")