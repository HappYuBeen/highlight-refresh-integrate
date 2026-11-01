"""요약 프롬프트 테스트 · summarize_sections, summarize_three_lines (김희진)

가짜 모델(FakeToolChatModel)로 바꿔 끼워 네트워크·API 키 없이 실행한다.
summary_prompt는 get_chat_model을 모듈 맨 위에서 import하므로
hri.llm.llm_client가 아니라 hri.prompt.summary_prompt.get_chat_model을 바꿔야 한다.
"""

import pytest
from langchain_core.runnables import RunnableLambda

from hri.domain.schemas import Section, SectionSummary, ThreeLineSummary
from hri.exception.errors import OutputFormatError
from hri.prompt import summary_prompt
from hri.prompt.summary_prompt import (
	MAX_SECTION_CHARS,
	SECTION_MAX_TOKENS,
	THREE_LINES_MAX_TOKENS,
	summarize_sections,
	summarize_three_lines,
)
from tests.fakes import FakeToolChatModel


def _section(no=0, title="1. 개요", text="본문입니다", start=1, end=2) -> Section:
	return Section(section_no=no, title=title, start_page=start, end_page=end, text=text)


def _sec_summary(summary="요약입니다", title="LLM이 쓴 제목") -> SectionSummary:
	return SectionSummary(title=title, summary=summary)


def _three(lines=("핵심 주장", "근거 수치 2.1%", "시사점")) -> ThreeLineSummary:
	return ThreeLineSummary(reasoning="r", lines=list(lines))


def _text(message) -> str:
	"""구조화 출력 체인에는 ('human', 글) 튜플이 그대로 들어오므로 둘 다 처리한다."""
	return message[1] if isinstance(message, tuple) else message.content


@pytest.fixture
def install(monkeypatch):
	"""get_chat_model을 가짜로 바꾼다. (가짜 모델, get_chat_model에 넘어온 인자 목록)을 돌려준다."""

	def _install(*responses):
		fake = FakeToolChatModel(responses=list(responses))
		kwargs_log: list[dict] = []

		def factory(**kwargs):
			kwargs_log.append(kwargs)
			return fake

		monkeypatch.setattr(summary_prompt, "get_chat_model", factory)
		return fake, kwargs_log

	return _install


# ─────────────────────────────────────────────────────────────
# summarize_sections
# ─────────────────────────────────────────────────────────────

def test_섹션이_없으면_LLM을_부르지_않고_빈_목록(install):
	fake, kwargs_log = install()
	assert summarize_sections([]) == []
	assert kwargs_log == []
	assert fake.calls == []


def test_제목과_쪽수는_섹션_값으로_채운다(install):
	install(_sec_summary(summary="  공백 있는 요약  ", title="LLM이 바꾼 제목"))
	[result] = summarize_sections([_section(no=3, title="Ⅱ. 원문 제목", start=5, end=7)])
	assert result.title == "Ⅱ. 원문 제목"          # LLM 제목이 아니라 원문 제목
	assert result.summary == "공백 있는 요약"       # 앞뒤 공백 제거
	assert (result.section_id, result.start_page, result.end_page) == (3, 5, 7)


def test_여러_섹션은_입력_순서대로_요약한다(install):
	fake, _ = install(_sec_summary("첫째"), _sec_summary("둘째"))
	results = summarize_sections([_section(0, "A"), _section(1, "B")])
	assert [r.summary for r in results] == ["첫째", "둘째"]
	assert [r.title for r in results] == ["A", "B"]
	assert len(fake.calls) == 2


def test_모델은_temperature_0과_섹션용_max_tokens로_만든다(install):
	_, kwargs_log = install(_sec_summary())
	summarize_sections([_section()])
	assert kwargs_log == [{"temperature": 0, "max_tokens": SECTION_MAX_TOKENS}]


def test_본문이_길면_앞부분만_보낸다(install):
	fake, _ = install(_sec_summary())
	summarize_sections([_section(text="가" * (MAX_SECTION_CHARS + 500))])
	sent = _text(fake.calls[0][1])
	assert "가" * MAX_SECTION_CHARS in sent
	assert "가" * (MAX_SECTION_CHARS + 1) not in sent


def test_첫_응답이_비어_있으면_한_번_더_시도한다(install):
	fake, _ = install(_sec_summary("   "), _sec_summary("두 번째 시도"))
	[result] = summarize_sections([_section()])
	assert result.summary == "두 번째 시도"
	assert len(fake.calls) == 2


def test_계속_형식이_틀리면_OutputFormatError(install):
	install(_sec_summary(""), _sec_summary(""))
	with pytest.raises(OutputFormatError, match="section_no=4"):
		summarize_sections([_section(no=4)])


def test_호출_예외도_재시도하고_성공하면_통과(monkeypatch):
	calls = {"n": 0}

	def flaky(messages):
		calls["n"] += 1
		if calls["n"] == 1:
			raise RuntimeError("일시 오류")
		return _sec_summary("복구됨")

	class _Model:
		def with_structured_output(self, schema):
			return RunnableLambda(flaky)

	monkeypatch.setattr(summary_prompt, "get_chat_model", lambda **kw: _Model())
	[result] = summarize_sections([_section()])
	assert result.summary == "복구됨"
	assert calls["n"] == 2


# ─────────────────────────────────────────────────────────────
# summarize_three_lines
# ─────────────────────────────────────────────────────────────

def test_섹션_요약이_없으면_ValueError(install):
	_, kwargs_log = install()
	with pytest.raises(ValueError):
		summarize_three_lines([])
	assert kwargs_log == []


def test_3줄을_돌려주고_앞뒤_공백은_뗀다(install):
	install(_three(lines=(" 핵심 ", "근거 ", " 시사점")))
	result = summarize_three_lines([_sec_summary()])
	assert result.lines == ["핵심", "근거", "시사점"]


def test_입력은_섹션_제목과_요약을_순서대로_담는다(install):
	fake, _ = install(_three())
	summarize_three_lines([_sec_summary("첫 요약", title="A"), _sec_summary("둘째 요약", title="B")])
	body = _text(fake.calls[0][1])
	assert body.index("[A]\n첫 요약") < body.index("[B]\n둘째 요약")


def test_3줄_모델은_temperature_0과_3줄용_max_tokens로_만든다(install):
	_, kwargs_log = install(_three())
	summarize_three_lines([_sec_summary()])
	assert kwargs_log == [{"temperature": 0, "max_tokens": THREE_LINES_MAX_TOKENS}]


def test_빈_줄이_있으면_한_번_더_시도한다(install):
	fake, _ = install(_three(lines=("핵심", " ", "시사점")), _three())
	result = summarize_three_lines([_sec_summary()])
	assert result.lines == ["핵심 주장", "근거 수치 2.1%", "시사점"]
	assert len(fake.calls) == 2


def test_계속_빈_줄이면_OutputFormatError(install):
	install(_three(lines=("핵심", "", "시사점")), _three(lines=("", "근거", "시사점")))
	with pytest.raises(OutputFormatError):
	    summarize_three_lines([_sec_summary()])