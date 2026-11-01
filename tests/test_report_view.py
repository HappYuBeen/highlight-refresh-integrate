"""보고서 목록 화면 테스트 · on_report_search, on_report_select, on_view_summary, on_go_chat"""
from datetime import date

import gradio as gr

from hri.domain.schemas.report import Report
from hri.view import report_view
from hri.view.report_view import (
	NEED_SELECT_MESSAGE,
	NO_PUBLISHER,
	NO_RESULT_MESSAGE,
	build_report_list_panel,
	on_go_chat,
	on_report_search,
	on_report_select,
	on_view_summary,
)


def _report(report_id, title, publisher="HRI", published=date(2026, 7, 31), chunks=3):
	return Report(
		report_id=report_id, title=title, publisher=publisher, published_date=published,
		chunk_count=chunks, file_path="data/raw/a.pdf", uploaded_by="u1",
	)


def _select_event(row, row_value):
	return gr.SelectData(None, {"index": [row, 0], "value": row_value[0], "row_value": row_value})


def test_search_builds_rows_and_ids(monkeypatch):
	monkeypatch.setattr(report_view, "search_reports", lambda k, user_id=None: [_report(7, "A보고서"), _report(5, "B보고서", None, None, 0)])
	rows, ids, message = on_report_search("보고서")
	assert rows == [["A보고서", "HRI", "2026-07-31", 3], ["B보고서", NO_PUBLISHER, "", 0]]
	assert ids == [7, 5]
	assert "2" in message


def test_search_no_result(monkeypatch):
	monkeypatch.setattr(report_view, "search_reports", lambda k, user_id=None: [])
	assert on_report_search("없음") == ([], [], NO_RESULT_MESSAGE)


def test_select_puts_report_id_in_state():
	state = {"user": "u1", "report_ids": []}
	new_state, message = on_report_select(_select_event(1, ["B보고서", "HRI", "", 0]), [7, 5], state)
	assert new_state["report_ids"] == [5]
	assert new_state["user"] == "u1"
	assert state["report_ids"] == []  # 원래 state는 바꾸지 않는다
	assert "B보고서" in message


def test_select_out_of_range():
	state = {"report_ids": []}
	new_state, message = on_report_select(_select_event(3, ["x"]), [7], state)
	assert new_state == state
	assert message == NEED_SELECT_MESSAGE


def test_go_buttons_move_tab_with_selected_report(monkeypatch):
	monkeypatch.setattr(report_view, "search_reports", lambda *a, **k: [_report(5, "A"), _report(6, "B")])
	tabs, dropdown = on_view_summary({"report_ids": [5, 9]})
	assert tabs["selected"] == report_view.SUMMARY_TAB_ID
	# 선택지도 함께 채우고, 값은 목록에 있는 번호만 리스트로 (CheckboxGroup)
	assert dropdown["choices"] == [("A", 5), ("B", 6)] and dropdown["value"] == [5]
	tabs, dropdown = on_go_chat({"report_ids": [5]})
	assert tabs["selected"] == report_view.CHAT_TAB_ID and dropdown["value"] == [5]


def test_go_buttons_without_selection_stay(monkeypatch):
	warned = []
	monkeypatch.setattr(report_view.gr, "Warning", lambda m, *a, **k: warned.append(m))
	for go in (on_view_summary, on_go_chat):
		tabs, dropdown = go({"report_ids": []})
		assert "selected" not in tabs and "value" not in dropdown
	assert warned == [NEED_SELECT_MESSAGE] * 2


def test_build_panel_returns_column_and_two_buttons():
	with gr.Blocks():
		panel, summary_btn, chat_btn = build_report_list_panel()
	assert isinstance(panel, gr.Column)
	assert isinstance(summary_btn, gr.Button) and isinstance(chat_btn, gr.Button)
	assert {"table", "listed_ids", "message"} <= set(report_view.report_list_components)