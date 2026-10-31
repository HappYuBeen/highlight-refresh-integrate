"""on_compare 로직 테스트 · 가짜 비교표·차트 사용 (장유빈)"""
import gradio as gr
import pytest

from hri.view import indicator_view
from hri.view.indicator_view import (
	COMPARE_ERROR_MESSAGE,
	NEED_MORE_REPORTS_MESSAGE,
	NO_CHART_NOTE,
	NO_INDICATORS_MESSAGE,
	on_compare,
	on_compare_picked,
)


def _use(monkeypatch, table="| 표 |", image="data/plots/compare_x.png"):
	"""build_compare_table·plot_indicators를 가짜로 바꾸고 받은 인자를 기록해 돌려준다."""
	calls = {"table": [], "plot": []}

	def fake_table(ids):
		calls["table"].append(ids)
		return table

	def fake_plot(ids):
		calls["plot"].append(ids)
		return image

	monkeypatch.setattr(indicator_view, "build_compare_table", fake_table)
	monkeypatch.setattr(indicator_view, "plot_indicators", fake_plot)
	monkeypatch.setattr(indicator_view, "build_ecos_reference", lambda ids: "ECOS")
	return calls


@pytest.mark.parametrize("ids", [None, [], [1], ["1"], [2, 2]])
def test_fewer_than_two_reports_shows_message(monkeypatch, ids):
	calls = _use(monkeypatch)
	assert on_compare(ids) == (NEED_MORE_REPORTS_MESSAGE, "", None, "")
	assert calls == {"table": [], "plot": []}


def test_two_reports_return_table_and_image(monkeypatch):
	calls = _use(monkeypatch)
	assert on_compare([1, 2]) == ("| 표 |", "ECOS", "data/plots/compare_x.png", "")
	assert calls == {"table": [[1, 2]], "plot": [[1, 2]]}


def test_ids_are_converted_to_int(monkeypatch):
	calls = _use(monkeypatch)
	on_compare(["3", 4.0])
	assert calls["table"] == [[3, 4]]


def test_no_indicators_skips_chart(monkeypatch):
	calls = _use(monkeypatch, table="")
	assert on_compare([1, 2]) == (NO_INDICATORS_MESSAGE, "", None, "")
	assert calls["plot"] == []


def test_no_chart_moves_note_to_chart(monkeypatch):
	_use(monkeypatch, image=None)
	assert on_compare([1, 2]) == ("| 표 |", "ECOS", None, NO_CHART_NOTE)


@pytest.mark.parametrize("target", ["build_compare_table", "plot_indicators"])
def test_error_shows_message(monkeypatch, target):
	_use(monkeypatch)

	def boom(ids):
		raise RuntimeError("실패")

	monkeypatch.setattr(indicator_view, target, boom)
	assert on_compare([1, 2]) == (COMPARE_ERROR_MESSAGE, "", None, "")


def test_progress_is_reported(monkeypatch):
	_use(monkeypatch)
	seen = []
	on_compare([1, 2], progress=lambda fraction, desc=None: seen.append((fraction, desc)))
	assert [f for f, _ in seen] == [0.1, 0.7, 0.9]


def test_works_as_gradio_event_handler(monkeypatch):
	_use(monkeypatch)
	with gr.Blocks():
		dropdown = gr.Dropdown(choices=[("A", 1), ("B", 2)], multiselect=True)
		button = gr.Button()
		table, ecos, image, note = gr.Markdown(), gr.Markdown(), gr.Image(), gr.Markdown()
		button.click(on_compare, inputs=dropdown, outputs=[table, ecos, image, note])


# ---------------- on_compare_picked: 비교 차트 칸을 펼치고, 차트가 없으면 이미지 칸을 숨기고 안내 문구만 ----------------
def test_picked_hides_image_and_shows_note_without_chart(monkeypatch):
	_use(monkeypatch, image=None)
	table, ecos, image, note, fold = on_compare_picked({"selected": [1, 2]}, progress=lambda *a, **k: None)
	assert image["visible"] is False and note == gr.update(value=NO_CHART_NOTE, visible=True)
	assert fold == gr.update(open=True, elem_classes=["compare-fold", "compare-fold-fit"])


def test_picked_shows_image_and_hides_note_with_chart(monkeypatch):
	_use(monkeypatch)
	table, ecos, image, note, fold = on_compare_picked({"selected": [1, 2]}, progress=lambda *a, **k: None)
	assert image == gr.update(value="data/plots/compare_x.png", visible=True) and note["visible"] is False
	assert fold == gr.update(open=True, elem_classes=["compare-fold"])

def test_picked_shows_table_message_in_chart_when_no_indicators(monkeypatch):
	_use(monkeypatch, table="")
	_, _, image, note, fold = on_compare_picked({"selected": [1, 2]}, progress=lambda *a, **k: None)
	assert image["visible"] is False and note == gr.update(value=NO_INDICATORS_MESSAGE, visible=True)
	assert "compare-fold-fit" in fold["elem_classes"]
