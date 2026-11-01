"""용어 풀이 · explain_term (류현수)

경제 용어 1개의 원문 정의를 ECOS 통계용어 사전에서 찾아오고, 그 정의만 근거로
페르소나 난이도에 맞춰 쉽게 다시 써서 TermCard(term, definition, explanation, source)로 돌려준다.
(terms 캐시·표준국어대사전은 팀 결정으로 쓰지 않는다)

흐름
	1) 입력 확인: 공백을 떼고, 비었거나 너무 길면 InvalidInputError
	2) 원문 정의 찾기: ecos_client.lookup_term() (ECOS 통계용어)
	3) 쉬운 설명 만들기: PERSONAS 말투로, 원문 정의에 있는 내용만 2~3문장으로 다시 쓴다
	4) TermCard로 묶어 반환. 사전에 없는 용어면 None

사용 예
	card = explain_term("GDP", persona="입문자")
	if card is None:
		... "통계용어 사전에서 찾지 못한 용어입니다." 안내
"""

from ecos import EcosClient

from hri.api import ecos_client
from hri.domain.schemas.external import TermCard
from hri.exception.errors import ExternalApiError, InvalidInputError, OutputFormatError
from hri.prompt.persona_prompt import PERSONAS

__all__ = ["explain_term", "MAX_TERM_LENGTH"]

# 용어 입력 최대 길이: 용어 검색 창에는 단어 하나만 넣는다 (문장이 들어오면 막는다)
MAX_TERM_LENGTH = 30

# 용어 풀이용 규칙 system. 페르소나 문구(PERSONAS) 뒤에 붙인다
_TERM_RULE = (
	"지금은 보고서 질문이 아니라 경제 용어 하나를 풀이하는 작업입니다.\n"
	"[원문 정의]에 있는 내용만 근거로, 위 말투와 용어 난이도에 맞춰 2~3문장으로 다시 씁니다.\n"
	"- [원문 정의]에 없는 사실·수치·예시는 지어내지 않습니다.\n"
	"- 제목, 목록 기호, 따옴표 없이 설명 문장만 씁니다."
)


def _build_messages(term: str, definition: str, persona: str) -> list[dict]:
	"""페르소나 system + 용어 풀이 규칙 system + 용어·원문 정의 user 순서로 messages를 만든다."""
	return [
		{"role": "system", "content": PERSONAS[persona]},
		{"role": "system", "content": _TERM_RULE},
		{"role": "user", "content": f"[용어]\n{term}\n\n[원문 정의]\n{definition}"},
	]


def explain_term(term: str, persona: str = "실무자", client: EcosClient | None = None) -> TermCard | None:
	"""
	경제 용어 1개를 ECOS 통계용어에서 찾아 페르소나 난이도에 맞춘 쉬운 설명과 함께 TermCard로 돌려준다.

	입력
		term: 용어 (앞뒤 공백은 뗀다, 최대 MAX_TERM_LENGTH자)
		persona: "입문자" / "실무자" / "전문가". PERSONAS에 없으면 "실무자"
		client: 재사용할 EcosClient (None이면 lookup_term이 새로 만든다)

	반환
		TermCard. ECOS 통계용어에 없는 용어면 None

	예외
		InvalidInputError: 빈 입력 · 공백 입력 · 최대 길이 초과
		ExternalApiError: ECOS 호출이 실패했을 때 (없는 용어와 구분)
		OutputFormatError: LLM이 빈 설명을 돌려줬을 때

	사용하는 곳: term_view.on_term_search(), chat_view.on_term_click()
	"""
	# 1) 입력 확인
	word = (term or "").strip()
	if not word:
		raise InvalidInputError("용어를 입력해 주세요.")
	if len(word) > MAX_TERM_LENGTH:
		raise InvalidInputError(f"용어는 {MAX_TERM_LENGTH}자 이내로 입력해 주세요.")
	if persona not in PERSONAS:
		persona = "실무자"

	# 2) 원문 정의 찾기 (검색 결과 없음은 lookup_term이 None으로, 그 밖의 오류는 예외로 알려 준다)
	try:
		found = ecos_client.lookup_term(word, client)
	except Exception as e:  # ECOS 응답 오류·네트워크 오류 등 종류가 다양해서 넓게 잡는다
		raise ExternalApiError(f"ECOS 통계용어를 불러오지 못했습니다. ({e})") from e
	if not found or not str(found.get("definition", "")).strip():
		return None
	definition = str(found["definition"]).strip()
	# ECOS 표제어가 있으면 그 이름을 쓴다 (예: 'gdp' 검색 → 'GDP')
	title = str(found.get("term") or word).strip()

	# 3) 쉬운 설명 만들기 (llm_client는 import할 때 .env가 필요해서 함수 안에서 import)
	from hri.llm.llm_client import ask_llm

	explanation, _ = ask_llm(_build_messages(title, definition, persona), temperature=0.2)
	explanation = (explanation or "").strip()
	if not explanation:
		raise OutputFormatError(f"'{title}' 용어 설명을 만들지 못했습니다.")

	# 4) TermCard로 묶기
	return TermCard(
		term=title,
		definition=definition,
		explanation=explanation,
		source=str(found.get("source") or "ECOS 통계용어"),
	)
