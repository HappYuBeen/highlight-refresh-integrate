"""질의응답 화면 로직 테스트 · 가짜 ask_stream·보고서 목록 사용 (장유빈)"""
import copy

import gradio as gr

from hri.domain.schemas import AnswerResult
from hri.view import chat_view
from hri.view.chat_view import (
	ERROR_MESSAGE,
	NEED_LOGIN_MESSAGE,
	_sources_to_markdown,
	_suggest_updates,
	_user_id,
	build_chat_tab,
	connect_chat_events,
	refresh_report_choices,
	respond_stream,
)


def _state(**kwargs) -> dict:
	base = {"user": {"user_id": "u1"}, "session_id": "s1", "report_ids": [], "last_log_id": None}
	base.update(kwargs)
	return base


def _result(**kwargs) -> AnswerResult:
	base = dict(question="q", answer="최종 답변", route="rag", log_id=5)
	base.update(kwargs)
	return AnswerResult(**base)


def _source(**kwargs) -> dict:
	base = dict(report_id=1, report_title="보고서A", page_start=5, page_end=7, kind="text", label="2. 물가", needs_review=False)
	base.update(kwargs)
	return base


def _use_stream(monkeypatch, chunks):
	"""ask_stream을 정해 둔 조각을 돌려주는 가짜로 바꾸고, 받은 인자를 기록해 돌려준다."""
	received = {}

	def fake(**kwargs):
		received.update(kwargs)
		yield from chunks

	monkeypatch.setattr(chat_view, "_ask_stream", fake)
	return received


def _run(**kwargs):
	"""respond_stream을 끝까지 돌려 yield된 값을 모두 모은다 (yield 시점의 값을 복사해 둔다)."""
	args = dict(message="질문", history=[], persona="실무자", report_ids=[], k=4, use_mmr=False, state=_state())
	args.update(kwargs)
	return [copy.deepcopy(output) for output in respond_stream(**args)]


# ---------------- respond_stream ----------------
def test_streams_text_and_final_result(monkeypatch):
	_use_stream(monkeypatch, ["안", "녕", _result(answer="안녕", sources=[_source()], suggestions=["다음 질문?"], log_id=9)])
	outputs = _run()
	# 처음: 입력칸 비우기 + 첫 글자 조각이 올 때까지 준비 중 문구
	assert outputs[0][1] == ""
	assert outputs[0][0][-2:] == [{"role": "user", "content": "질문"}, {"role": "assistant", "content": chat_view.THINKING_MESSAGE}]
	# 글자가 이어 붙는다
	assert [o[0][-1]["content"] for o in outputs[1:3]] == ["안", "안녕"]
	# 마지막: 최종 답변·출처·추천 질문·기록 번호
	history, _, sources, s1, s2, s3, state = outputs[-1]
	assert history[-1]["content"] == "안녕"
	assert "보고서A" in sources
	assert s1["value"] == "다음 질문?" and s1["visible"] is True
	assert s2["visible"] is False and s3["visible"] is False
	assert state["last_log_id"] == 9


def test_every_yield_has_seven_outputs(monkeypatch):
	_use_stream(monkeypatch, ["a", _result(answer="a")])
	assert all(len(o) == 7 for o in _run())  # chatbot, message, sources, 추천 버튼 3개, state


def test_passes_arguments_to_ask_stream(monkeypatch):
	received = _use_stream(monkeypatch, [_result()])
	_run(message="금리는?", persona="전문가", report_ids=[3, 4], k=6, use_mmr=True, state=_state(session_id="abc"))
	assert received == {
		"question": "금리는?", "persona": "전문가", "session_id": "abc", "user_id": "u1",
		"report_ids": [3, 4], "k": 6, "use_mmr": True,
	}


def test_empty_report_selection_becomes_none(monkeypatch):
	received = _use_stream(monkeypatch, [_result()])
	_run(report_ids=[])
	assert received["report_ids"] is None


def test_previous_turns_are_kept(monkeypatch):
	_use_stream(monkeypatch, [_result(answer="두 번째 답")])
	old = [{"role": "user", "content": "첫 질문"}, {"role": "assistant", "content": "첫 답"}]
	history = _run(history=old)[-1][0]
	assert [m["content"] for m in history] == ["첫 질문", "첫 답", "질문", "두 번째 답"]
	assert len(old) == 2  # 받은 목록을 바꾸지 않는다


def test_no_answer_result_has_no_sources_and_hidden_suggestions(monkeypatch):
	_use_stream(monkeypatch, ["자료에 없습니다", _result(answer="자료에 없습니다", route="no_answer", log_id=3)])
	_, _, sources, s1, s2, s3, state = _run()[-1]
	assert sources == ""
	assert all(s["visible"] is False for s in (s1, s2, s3))
	assert state["last_log_id"] == 3


def test_new_question_clears_previous_sources_and_suggestions(monkeypatch):
	_use_stream(monkeypatch, [_result()])
	first = _run()[0]
	assert first[2] == ""
	assert all(b["visible"] is False for b in first[3:6])


def test_result_answer_replaces_streamed_text(monkeypatch):
	_use_stream(monkeypatch, ["임시", _result(answer="최종")])
	assert _run()[-1][0][-1]["content"] == "최종"


def test_stream_without_final_result_keeps_streamed_text(monkeypatch):
	_use_stream(monkeypatch, ["가", "나"])
	assert _run()[-1][0][-1]["content"] == "가나"


def test_error_shows_message_and_keeps_going(monkeypatch):
	def boom(**kwargs):
		raise RuntimeError("실패")
		yield  # 제너레이터로 만들기 위한 줄

	monkeypatch.setattr(chat_view, "_ask_stream", boom)
	outputs = _run()
	assert outputs[-1][0][-1]["content"] == ERROR_MESSAGE


def test_error_midway_replaces_partial_answer(monkeypatch):
	def partial(**kwargs):
		yield "절반"
		raise RuntimeError("실패")

	monkeypatch.setattr(chat_view, "_ask_stream", partial)
	assert _run()[-1][0][-1]["content"] == ERROR_MESSAGE


def test_not_logged_in_does_not_call_ask_stream(monkeypatch):
	received = _use_stream(monkeypatch, [_result()])
	outputs = _run(state=_state(user=None))
	assert received == {}
	assert outputs[-1][0][-1]["content"] == NEED_LOGIN_MESSAGE


# ---------------- 도우미 ----------------
def test_user_id_from_dict_object_and_none():
	class _User:
		user_id = "obj"

	assert _user_id({"user": {"user_id": "d"}}) == "d"
	assert _user_id({"user": _User()}) == "obj"
	assert _user_id({"user": None}) is None
	assert _user_id({}) is None


def test_sources_markdown_single_page_range_chart_and_table():
	md = _sources_to_markdown([
		_source(),
		_source(page_start=9, page_end=9, kind="chart", label="그림 1(차트) (추정값)"),
		_source(page_start=2, page_end=2, kind="table", label=""),
	])
	assert md.splitlines() == [
		"**출처**",
		"- 보고서A · p.5-7 · 2. 물가",
		"- 보고서A · p.9 · 그림 1(차트) (추정값)",
		"- 보고서A · p.2 · 표",
	]


def test_sources_markdown_empty():
	assert _sources_to_markdown([]) == ""


def test_suggest_updates_show_only_given_questions():
	updates = _suggest_updates(["a", "b"])
	assert [u["visible"] for u in updates] == [True, True, False]
	assert updates[0]["value"] == "a" and updates[2]["value"] == ""


# ---------------- refresh_report_choices ----------------
class _R:
	def __init__(self, report_id, title):
		self.report_id, self.title = report_id, title


def _use_reports(monkeypatch):
	monkeypatch.setattr(chat_view, "search_reports", lambda keyword="": [_R(1, "A"), _R(2, "B")])


def test_refresh_fills_choices_and_uses_state_selection(monkeypatch):
	_use_reports(monkeypatch)
	update = refresh_report_choices(_state(report_ids=[2]), [])
	assert update["choices"] == [("A", 1), ("B", 2)]
	assert update["value"] == [2]


def test_refresh_keeps_current_selection_over_state(monkeypatch):
	_use_reports(monkeypatch)
	assert refresh_report_choices(_state(report_ids=[2]), [1])["value"] == [1]


def test_refresh_drops_ids_not_in_list(monkeypatch):
	_use_reports(monkeypatch)
	assert refresh_report_choices(_state(report_ids=[2, 99]), [])["value"] == [2]


# ---------------- 화면 만들기·연결 ----------------
def test_build_and_connect_do_not_raise():
	with gr.Blocks():
		state = gr.State(_state())
		tab = build_chat_tab()
		connect_chat_events(state)
	assert isinstance(tab, gr.Tab)
	assert tab.id == "chat"
	assert tab.visible is True  # 로그인 전에는 build_app()이 탭 줄을 통째로 숨긴다
	components = chat_view.chat_components
	assert len(components["suggest_buttons"]) == 3
	assert components["persona"].value == "실무자"
	assert components["report_dropdown"].multiselect is True
	# 용어 검색은 처음에 접혀 있는 메뉴로 오른쪽 반에 둔다
	assert isinstance(components["term_menu"], gr.Accordion)
	assert components["term_menu"].open is False
	assert "term_buttons" not in components and "term_card" not in components