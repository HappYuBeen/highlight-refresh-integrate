"""질의응답 탭의 용어 검색(chat_view.build_term_panel·on_term_click) 테스트 · explain_term을 가짜로 바꿔 화면 문구만 확인 (류현수)"""

import gradio as gr
import pytest

from hri.domain.schemas import TermCard
from hri.exception.errors import ExternalApiError, InvalidInputError, OutputFormatError
from hri.view import chat_view

CARD = TermCard(term="GDP", definition="국내총생산.\n한 나라 안에서 생산된 가치의 합계.", explanation="나라 전체가 번 돈입니다.", source="ECOS 통계용어")


def _fake_explain(monkeypatch, result):
	"""explain_term을 result(TermCard·None·예외)를 돌려주는 가짜로 바꾸고, 받은 인자는 calls에 기록"""
	calls = []

	def fake(term, persona="실무자"):
		calls.append((term, persona))
		if isinstance(result, Exception):
			raise result
		return result

	monkeypatch.setattr(chat_view, "explain_term", fake)
	return calls


# [확인 1] 찾으면 용어·쉬운 설명·원문 정의(인용)·출처를 보여 준다
def test_shows_card(monkeypatch):
	calls = _fake_explain(monkeypatch, CARD)
	md = chat_view.on_term_click("GDP", "입문자")
	assert calls == [("GDP", "입문자")]
	assert "#### GDP" in md and "나라 전체가 번 돈입니다." in md
	assert "(ECOS 통계용어)" in md
	assert "> 국내총생산.\n> 한 나라 안에서" in md  # 여러 줄 정의도 모두 인용


# [확인 2] 페르소나가 비어 오면 실무자로 부른다
def test_default_persona(monkeypatch):
	calls = _fake_explain(monkeypatch, CARD)
	chat_view.on_term_click("GDP", None)
	assert calls[0][1] == "실무자"


# [확인 3] 사전에 없으면 공백을 뗀 용어로 안내 문구
def test_not_found(monkeypatch):
	_fake_explain(monkeypatch, None)
	assert "'없는용어'을(를) 찾지 못했습니다" in chat_view.on_term_click(" 없는용어 ", "실무자")


# [확인 4] 오류는 화면 문구로 바꾸고, API 오류의 자세한 내용은 보여 주지 않는다
@pytest.mark.parametrize(
	("error", "expected"),
	[
		(InvalidInputError("용어를 입력해 주세요."), "용어를 입력해 주세요."),
		(ExternalApiError("ECOS 통계용어를 불러오지 못했습니다. (INFO-100 인증키 오류)"), chat_view.TERM_API_ERROR_MESSAGE),
		(OutputFormatError("빈 설명"), chat_view.TERM_FAILED_MESSAGE),
	],
)
def test_errors_become_messages(monkeypatch, error, expected):
	_fake_explain(monkeypatch, error)
	md = chat_view.on_term_click("GDP", "실무자")
	assert md == f"⚠️ {expected}"
	assert "INFO-100" not in md


def _ancestors(block):
	"""block을 감싸는 레이아웃을 바깥쪽까지 모두 모은다 (gradio가 입력칸을 Form으로 한 번 더 감싸기 때문)"""
	found = []
	while block.parent is not None:
		block = block.parent
		found.append(block)
	return found


# [확인 5] 질의응답 탭을 만들면 '용어 검색' 메뉴 안에 검색 창이 생기고, 안내 문구(준비 중)는 없다
def test_build_chat_tab_has_term_panel():
	with gr.Blocks():
		chat_view.build_chat_tab()
	term = chat_view.term_components
	assert isinstance(term["term_input"], gr.Textbox)
	assert isinstance(term["term_btn"], gr.Button)
	assert isinstance(term["term_result"], gr.Markdown)
	menu = chat_view.chat_components["term_menu"]
	assert menu in _ancestors(term["term_input"]) and menu in _ancestors(term["term_result"])
	assert all("준비 중" not in str(getattr(child, "value", "")) for child in menu.children)
