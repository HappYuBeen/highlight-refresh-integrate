"""용어 풀이(term_service.explain_term) 테스트 · 네트워크·API 키 없이 가짜 ECOS·가짜 LLM으로 확인 (류현수)"""

import sys
import types

import pytest

from hri.api import ecos_client
from hri.domain.schemas.external import TermCard
from hri.exception.errors import ExternalApiError, InvalidInputError, OutputFormatError
from hri.prompt.persona_prompt import PERSONAS
from hri.service.term_service import MAX_TERM_LENGTH, explain_term

GDP = {"term": "GDP", "definition": "국내총생산. 일정 기간 한 나라 안에서 생산된 최종 재화와 서비스의 시장가치 합계.", "source": "ECOS 통계용어"}


@pytest.fixture
def fake_llm(monkeypatch):
	"""진짜 llm_client(.env 필요, OpenAI 호출) 대신 끼우는 가짜 모듈. 받은 messages는 calls에, 돌려줄 답은 answers에"""
	calls, answers = [], []

	def ask_llm(messages, temperature=0.3, max_tokens=500):
		calls.append(messages)
		return answers.pop(0) if answers else "쉬운 설명입니다.", {}

	module = types.ModuleType("hri.llm.llm_client")
	module.ask_llm = ask_llm
	monkeypatch.setitem(sys.modules, "hri.llm.llm_client", module)
	return calls, answers


@pytest.fixture
def fake_ecos(monkeypatch):
	"""진짜 ECOS 대신 result[0]을 돌려주는 lookup_term. 예외 객체를 넣으면 raise하고, 받은 용어는 asked에"""
	result, asked = [GDP], []

	def lookup_term(word, client=None):
		asked.append(word)
		if isinstance(result[0], Exception):
			raise result[0]
		return result[0]

	monkeypatch.setattr(ecos_client, "lookup_term", lookup_term)
	return result, asked


# [확인 1] 정의를 찾으면 TermCard를 돌려주고, 원문 정의가 LLM 입력에 들어간다
def test_returns_term_card(fake_llm, fake_ecos):
	calls, answers = fake_llm
	answers.append("  한 나라 안에서 1년 동안 만든 물건과 서비스의 값을 모두 더한 것입니다.  ")
	card = explain_term("GDP", persona="입문자")
	assert isinstance(card, TermCard)
	assert card.term == "GDP"
	assert card.definition == GDP["definition"]
	assert card.source == "ECOS 통계용어"
	assert card.explanation == "한 나라 안에서 1년 동안 만든 물건과 서비스의 값을 모두 더한 것입니다."
	assert calls[0][0]["content"] == PERSONAS["입문자"]
	assert GDP["definition"] in calls[0][-1]["content"]


# [확인 2] 앞뒤 공백을 뗀 용어로 ECOS를 찾는다
def test_strips_term(fake_llm, fake_ecos):
	_, asked = fake_ecos
	explain_term("  GDP ")
	assert asked == ["GDP"]


# [확인 3] ECOS에 없으면(None·빈 정의) None, LLM은 부르지 않는다
@pytest.mark.parametrize("missing", [None, {"term": "x", "definition": "  ", "source": "ECOS 통계용어"}])
def test_not_found_returns_none(fake_llm, fake_ecos, missing):
	calls, _ = fake_llm
	result, _ = fake_ecos
	result[0] = missing
	assert explain_term("없는용어") is None
	assert calls == []


# [확인 4] ECOS 호출이 실패하면 ExternalApiError
def test_ecos_error_raises(fake_llm, fake_ecos):
	result, _ = fake_ecos
	result[0] = RuntimeError("ECOS 장애")
	with pytest.raises(ExternalApiError, match="ECOS 장애"):
		explain_term("GDP")


# [확인 5] 빈 입력·공백·길이 초과는 InvalidInputError (ECOS를 부르지 않는다)
@pytest.mark.parametrize("term", ["", "   ", None, "가" * (MAX_TERM_LENGTH + 1)])
def test_invalid_input(fake_llm, fake_ecos, term):
	_, asked = fake_ecos
	with pytest.raises(InvalidInputError):
		explain_term(term)
	assert asked == []


# [확인 6] 모르는 페르소나는 실무자로 바꾼다
def test_unknown_persona_uses_default(fake_llm, fake_ecos):
	calls, _ = fake_llm
	explain_term("GDP", persona="초보")
	assert calls[0][0]["content"] == PERSONAS["실무자"]


# [확인 7] LLM이 빈 설명을 주면 OutputFormatError
def test_empty_explanation_raises(fake_llm, fake_ecos):
	_, answers = fake_llm
	answers.append("   ")
	with pytest.raises(OutputFormatError):
		explain_term("GDP")
