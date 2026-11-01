"""질의응답 탭의 👍/👎 피드백(chat_view.on_feedback·connect_feedback_events, qa_log_dao.update_feedback) 테스트 (류현수)

DB 없이 가짜 update_feedback·가짜 연결로 확인한다.
"""

import gradio as gr
import pytest

from hri.dao import qa_log_dao
from hri.view import chat_view
from hri.view.chat_view import _hide_feedback, _show_feedback, on_feedback


def _state(last_log_id=None):
	return {"user": {"user_id": "u1"}, "session_id": "s1", "report_ids": [], "last_log_id": last_log_id}


@pytest.fixture
def saved(monkeypatch):
	"""chat_view.update_feedback을 가짜로 바꾼다. 받은 (log_id, feedback)은 calls에, 돌려줄 값·예외는 result[0]에"""
	calls, result = [], [True]

	def fake(log_id, feedback):
		calls.append((log_id, feedback))
		if isinstance(result[0], Exception):
			raise result[0]
		return result[0]

	monkeypatch.setattr(chat_view, "update_feedback", fake)
	return calls, result


@pytest.fixture
def toasts(monkeypatch):
	"""gr.Info·gr.Warning 알림을 기록한다 (이벤트 밖에서 부르면 Gradio가 경고를 내므로 바꿔 끼운다)."""
	shown = []
	monkeypatch.setattr(chat_view.gr, "Info", lambda msg, *a, **k: shown.append(("info", msg)))
	monkeypatch.setattr(chat_view.gr, "Warning", lambda msg, *a, **k: shown.append(("warning", msg)))
	return shown


# ---------------- on_feedback ----------------
# [확인 1] 👍/👎를 누르면 last_log_id로 저장하고, 누른 버튼을 강조한다
@pytest.mark.parametrize(("feedback", "like_variant", "dislike_variant"), [(1, "primary", "secondary"), (-1, "secondary", "primary")])
def test_saves_feedback(saved, toasts, feedback, like_variant, dislike_variant):
	calls, _ = saved
	like, dislike = on_feedback(feedback, _state(last_log_id=9))
	assert calls == [(9, feedback)]
	assert (like["variant"], dislike["variant"]) == (like_variant, dislike_variant)
	assert toasts == [("info", chat_view.FEEDBACK_SAVED_MESSAGE[feedback])]


# [확인 2] 기록 번호가 없으면 저장하지 않고 경고만
def test_no_log_id(saved, toasts):
	calls, _ = saved
	like, dislike = on_feedback(1, _state(last_log_id=None))
	assert calls == []
	assert like == dislike == gr.update()
	assert toasts == [("warning", chat_view.FEEDBACK_NO_ANSWER_MESSAGE)]


# [확인 3] 저장 실패(없는 기록·DB 오류)면 경고만, 버튼은 그대로
@pytest.mark.parametrize("result", [False, RuntimeError("DB 연결 실패")])
def test_save_failure(saved, toasts, result):
	_, holder = saved
	holder[0] = result
	like, dislike = on_feedback(-1, _state(last_log_id=9))
	assert like == dislike == gr.update()
	assert toasts == [("warning", chat_view.FEEDBACK_ERROR_MESSAGE)]


# ---------------- 버튼 보이기·숨기기 ----------------
# [확인 4] 새 질문을 보내면 버튼을 숨기고 질문 전 기록 번호를 기억한다
def test_hide_on_new_question():
	like, dislike, before = _hide_feedback(_state(last_log_id=4))
	assert like["visible"] is False and dislike["visible"] is False
	assert like["variant"] == "secondary"
	assert before == 4


# [확인 5] 답변이 끝나면 새 기록이 생긴 경우에만 보인다 (오류·로그인 안내처럼 기록이 그대로면 숨김)
@pytest.mark.parametrize(("before", "after", "visible"), [(None, 5, True), (4, 5, True), (4, 4, False), (None, None, False)])
def test_show_after_answer(before, after, visible):
	like, dislike = _show_feedback(_state(last_log_id=after), before)
	assert like["visible"] is visible and dislike["visible"] is visible


# [확인 6] 질의응답 탭을 만들고 연결하면 👍/👎 클릭이 on_feedback으로, 답변 뒤에 보이기가 연결된다
def test_events_connected():
	with gr.Blocks() as demo:
		state = gr.State(_state())
		chat_view.build_chat_tab()
		chat_view.connect_chat_events(state)
	c = chat_view.chat_components
	assert c["like_btn"].visible is False and c["dislike_btn"].visible is False
	# 질문 보내기(Enter·전송)는 숨기기 → respond_stream → 보이기 순서로 이어져 있다 (따로 연결하면 순서가 꼬인다)
	for trigger in ((c["message"]._id, "submit"), (c["send_btn"]._id, "click")):
		first = [(i, fn) for i, fn in demo.fns.items() if trigger in fn.targets]
		assert [fn.name for _, fn in first] == ["_hide_feedback"]
		chain, last_id = [], first[0][0]
		while True:
			nxt = [(i, fn) for i, fn in demo.fns.items() if fn.trigger_after == last_id]
			if not nxt:
				break
			last_id = nxt[0][0]
			chain.append(nxt[0][1].name)
		assert chain == ["respond_stream", "_show_feedback"]
	for button in (c["like_btn"], c["dislike_btn"]):
		clicks = [fn for fn in demo.fns.values() if (button._id, "click") in fn.targets]
		assert len(clicks) == 1
		assert [x._id for x in clicks[0].outputs] == [c["like_btn"]._id, c["dislike_btn"]._id]


# ---------------- qa_log_dao.update_feedback ----------------
class FakeCursor:
	"""UPDATE의 바뀐 줄 수(rowcount)와 SELECT 결과를 정해 두는 가짜 커서. 실행한 SQL은 executed에 기록"""

	def __init__(self, rowcount, exists):
		self.rowcount, self.exists, self.executed = rowcount, exists, []

	def execute(self, sql, values):
		self.executed.append((" ".join(sql.split()), values))

	def fetchone(self):
		return {"1": 1} if self.exists else None


class FakeConnection:
	def __init__(self, cursor):
		self._cursor, self.committed = cursor, False

	def cursor(self):
		return self._cursor

	def commit(self):
		self.committed = True

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		return False


def _use_db(monkeypatch, rowcount, exists=True):
	cursor = FakeCursor(rowcount, exists)
	conn = FakeConnection(cursor)
	monkeypatch.setattr(qa_log_dao, "get_connection", lambda: conn)
	return cursor, conn


# [확인 7] 피드백을 UPDATE하고 commit한다
def test_update_feedback_saves(monkeypatch):
	cursor, conn = _use_db(monkeypatch, rowcount=1)
	assert qa_log_dao.update_feedback(9, -1) is True
	assert cursor.executed == [("UPDATE qa_logs SET feedback = %s WHERE log_id = %s", (-1, 9))]
	assert conn.committed


# [확인 8] 같은 값으로 다시 눌러 바뀐 줄이 0이어도 기록이 있으면 True, 없는 기록이면 False
@pytest.mark.parametrize(("exists", "expected"), [(True, True), (False, False)])
def test_update_feedback_unchanged_or_missing(monkeypatch, exists, expected):
	_use_db(monkeypatch, rowcount=0, exists=exists)
	assert qa_log_dao.update_feedback(9, 1) is expected


# [확인 9] 1·-1이 아닌 값은 DB에 가기 전에 ValueError
@pytest.mark.parametrize("value", [0, 2, None])
def test_update_feedback_rejects_value(monkeypatch, value):
	cursor, _ = _use_db(monkeypatch, rowcount=1)
	with pytest.raises(ValueError):
		qa_log_dao.update_feedback(9, value)
	assert cursor.executed == []
