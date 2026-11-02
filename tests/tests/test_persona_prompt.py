"""
페르소나 프롬프트 테스트 (서동안)
persona_prompt.py의 PERSONAS·FEW_SHOT_EXAMPLES·get_persona_prompt()와,
build_messages()가 그 결과를 프롬프트 설계서 4장 순서대로 붙이는지 확인한다 (LLM·DB 없이 실행).

build_messages() 순서
	[0]      system      페르소나 문구                    ← get_persona_prompt
	[1]~[6]  user/assistant × 3  Few-shot                ← get_persona_prompt
	[7]      system      RULE_SYSTEM_PROMPT (근거 규칙)
	[8]~     이전 대화   trim_history: 최근 6턴(12개), 사용자 메시지로 시작
	[-1]     user        ###\n[자료]\n…\n###\n\n질문: …
"""

import re

import pytest
from langchain_core.messages import AIMessage, HumanMessage, convert_to_messages

from hri.prompt.persona_prompt import ANSWER_FORMATS, FEW_SHOT_EXAMPLES, PERSONAS, get_persona_prompt
from hri.prompt.rag_prompt import RULE_SYSTEM_PROMPT, build_messages

PERSONA_NAMES = ["입문자", "실무자", "전문가"]
NO_ANSWER = "자료에 없습니다."
CONTEXT = "[출처: 2026 경제전망 p.3-4 / 물가]\n2026년 소비자물가 상승률은 2.3%로 전망된다."
QUESTION = "2026년 물가 전망은?"
MAX_HISTORY_MESSAGES = 12  # 최근 6턴 = 질문·답변 6쌍
N_EXAMPLES = 3  # 페르소나별 few-shot 예시 수
PROMPT_LEN = 1 + 2 * N_EXAMPLES  # get_persona_prompt() 길이: system 1 + 예시 3쌍
RULE_INDEX = PROMPT_LEN  # build_messages()에서 규칙 system 위치


def _roles(messages) -> list[str]:
	"""dict·BaseMessage가 섞여 있어도 역할만 뽑는다 (system / human / ai)."""
	return [m.type for m in convert_to_messages(messages)]


def _numbers(text: str) -> set[str]:
	"""문장 속 숫자(정수·소수)를 모두 뽑는다. 예) '0.3~0.5%p' → {'0.3', '0.5'}

	'00.1처럼 따옴표로 시작하는 연.월 표기는 소수가 아니므로 '00년 1월로 바꾼 뒤 뽑는다.
	"""
	text = re.sub(r"'(\d{2})\.(\d{1,2})", r"'\1년 \2월", text)
	return set(re.findall(r"\d+(?:\.\d+)?", text))


def _turns(n: int, start: int = 0) -> list:
	"""질문·답변 n턴짜리 대화 기록을 만든다. 예) _turns(2) → [질문0, 답변0, 질문1, 답변1]"""
	history = []
	for i in range(start, start + n):
		history += [HumanMessage(f"질문{i}"), AIMessage(f"답변{i}")]
	return history


def _history_part(messages) -> list:
	"""build_messages 결과에서 이전 대화 부분(규칙 system 다음부터 마지막 질문 앞까지)만 꺼낸다."""
	return convert_to_messages(messages)[RULE_INDEX + 1:-1]


def _examples(persona: str) -> list[dict]:
	return FEW_SHOT_EXAMPLES[persona]


ANSWER_INDEXES = range(N_EXAMPLES - 1)  # ①·② 답변 예시 (③은 '자료에 없습니다.')
NO_ANSWER_INDEX = N_EXAMPLES - 1
ALL_EXAMPLES = [(p, i) for p in PERSONA_NAMES for i in ANSWER_INDEXES]


# =====================================================================
# 1. PERSONAS
# =====================================================================

# [확인 1] 키는 화면 gr.Radio 선택지와 글자가 같아야 한다
def test_persona_keys_match_radio_choices():
	assert list(PERSONAS) == PERSONA_NAMES
	assert list(FEW_SHOT_EXAMPLES) == PERSONA_NAMES


# [확인 2] 페르소나 문구는 2문장 이상이다 (말투·용어 난이도·설명 방식)
@pytest.mark.parametrize("persona", PERSONA_NAMES)
def test_persona_text_has_two_or_more_sentences(persona):
	assert PERSONAS[persona].count("다.") >= 2


# [확인 3] 페르소나 문구에는 근거 규칙을 쓰지 않는다 (규칙은 규칙 system 한 곳에서만)
@pytest.mark.parametrize("persona", PERSONA_NAMES)
def test_persona_text_has_no_grounding_rule(persona):
	for word in ("[자료]", "자료에 없습니다", "###"):
		assert word not in PERSONAS[persona]


# [확인 4] 세 페르소나 문구는 서로 달라야 한다 (같으면 난이도 차이가 없음)
def test_persona_texts_are_different():
	assert len(set(PERSONAS.values())) == 3


# =====================================================================
# 2. FEW_SHOT_EXAMPLES
# =====================================================================

# [확인 5] 페르소나마다 정확히 3쌍 (① 답변 ② 답변 ③ 자료에 없음)
@pytest.mark.parametrize("persona", PERSONA_NAMES)
def test_few_shot_has_three_pairs(persona):
	examples = _examples(persona)
	assert len(examples) == N_EXAMPLES
	for example in examples:
		assert set(example) == {"question", "answer"}


# [확인 6] 같은 순서의 예시는 세 페르소나가 같은 질문·같은 [자료]를 쓴다 (난이도 차이 비교용)
@pytest.mark.parametrize("index", range(N_EXAMPLES))
def test_example_questions_are_shared(index):
	assert len({_examples(p)[index]["question"] for p in PERSONA_NAMES}) == 1


# [확인 7] 예시 3개는 서로 다른 질문이고, ①·②의 답은 페르소나마다 다르다
@pytest.mark.parametrize("index", ANSWER_INDEXES)
def test_example_answers_are_different(index):
	assert len({_examples(p)[index]["answer"] for p in PERSONA_NAMES}) == 3
	assert len({e["question"] for e in _examples("실무자")}) == N_EXAMPLES


# [확인 8] ③의 답은 정확히 '자료에 없습니다.'이고, ①·②의 답에는 그 문구가 없다
@pytest.mark.parametrize("persona", PERSONA_NAMES)
def test_third_example_is_no_answer(persona):
	examples = _examples(persona)
	assert examples[NO_ANSWER_INDEX]["answer"] == NO_ANSWER
	for index in ANSWER_INDEXES:
		assert NO_ANSWER not in examples[index]["answer"]


# [확인 9] 예시 질문은 실제 호출과 같은 형식이다: ###\n[자료]\n…\n###\n\n질문: …
@pytest.mark.parametrize("persona", PERSONA_NAMES)
def test_few_shot_question_format(persona):
	for example in _examples(persona):
		assert re.fullmatch(r"###\n\[자료\]\n\[출처: .+?\][\s\S]+\n###\n\n질문: .+", example["question"])


# [확인 10] ①·② 예시 답은 '(출처: 보고서명 p.n)'으로 끝난다 (출처 표기 습관)
@pytest.mark.parametrize("persona,index", ALL_EXAMPLES)
def test_example_answer_ends_with_source(persona, index):
	assert re.search(r"\(출처: .+ p\.\d+\)$", _examples(persona)[index]["answer"])


# [확인 11] 예시 답에 나온 숫자는 모두 [자료]에 있다 (예시가 숫자를 지어내면 모델도 따라 함)
@pytest.mark.parametrize("persona,index", ALL_EXAMPLES)
def test_example_numbers_come_from_context(persona, index):
	example = _examples(persona)[index]
	missing = _numbers(example["answer"]) - _numbers(example["question"])
	assert not missing, f"{persona} 예시{index + 1} 답변에 [자료]에 없는 숫자: {missing}"


# [확인 12] 예시 [자료]는 6문장 이내 (설계서 3.3 길이 제한: 모든 호출에 들어가는 토큰)
@pytest.mark.parametrize("persona", PERSONA_NAMES)
def test_few_shot_context_is_short(persona):
	for example in _examples(persona):
		context = example["question"].split("###")[1]
		assert context.count("다.") <= 6


# =====================================================================
# 2-1. 페르소나 차이가 예시에 드러나는지
# =====================================================================
def _explanations(answer: str) -> list[str]:
	"""'용어(풀이)' 모양의 괄호 풀이를 뽑는다. 출처·숫자만 든 괄호는 뺀다."""
	return [m for m in re.findall(r"\(([^()]+)\)", answer) if not m.startswith("출처") and re.search(r"[가-힣]{2}", m)]


# [확인 33] 입문자: 예시마다 용어를 괄호로 2번 이상 풀고, 모형·통계 용어는 쓰지 않는다
@pytest.mark.parametrize("index", ANSWER_INDEXES)
def test_beginner_explains_terms(index):
	answer = _examples("입문자")[index]["answer"]
	assert len(_explanations(answer)) >= 2
	for word in ("VAR", "모형", "전가율", "원문 확인"):
		assert word not in answer


# [확인 34] 실무자: '결론: / 근거: / 시사점:' 세 줄 형식이고, 방법론·전제·원문 확인은 쓰지 않는다
@pytest.mark.parametrize("index", ANSWER_INDEXES)
def test_practitioner_three_line_format(index):
	answer = _examples("실무자")[index]["answer"]
	lines = answer.split("\n")
	assert [line.split(":")[0] for line in lines] == ["결론", "근거", "시사점"]
	for word in ("VAR", "모형", "전제", "원문 확인"):
		assert word not in answer


# [확인 35] 전문가: 실무자보다 항상 길고, 자료에 없는 전제는 '원문 확인이 필요'하다고 밝힌다
@pytest.mark.parametrize("index", ANSWER_INDEXES)
def test_expert_is_deeper_than_practitioner(index):
	expert = _examples("전문가")[index]["answer"]
	practitioner = _examples("실무자")[index]["answer"]
	assert len(expert) > len(practitioner) * 1.3
	assert "원문 확인이 필요" in expert
	assert not expert.startswith("결론:")


# [확인 36] 말투·난이도(PERSONAS)와 답변 구조(ANSWER_FORMATS)에 각자의 규칙이 들어 있다
def test_persona_texts_state_their_format():
	assert list(ANSWER_FORMATS) == PERSONA_NAMES
	assert "괄호" in PERSONAS["입문자"] and "결론을 먼저" in ANSWER_FORMATS["입문자"]
	assert "설명하지 않습니다" in PERSONAS["실무자"] and "'결론:'" in ANSWER_FORMATS["실무자"]
	assert "원문 확인" in PERSONAS["전문가"] and "전달 경로" in ANSWER_FORMATS["전문가"]


# [확인 37] 답변 구조는 질의응답 system에만 붙고, 용어 풀이가 쓰는 PERSONAS에는 없다
@pytest.mark.parametrize("persona", PERSONA_NAMES)
def test_answer_format_only_in_qa_prompt(persona):
	assert ANSWER_FORMATS[persona] not in PERSONAS[persona]
	assert get_persona_prompt(persona)[0]["content"].endswith(ANSWER_FORMATS[persona])
	for word in ("[자료]", "자료에 없습니다", "###"):
		assert word not in ANSWER_FORMATS[persona]


# =====================================================================
# 3. get_persona_prompt()
# =====================================================================

# [확인 13] 반환 순서: [system, (user, assistant) × 3]
@pytest.mark.parametrize("persona", PERSONA_NAMES)
def test_get_persona_prompt_order(persona):
	messages = get_persona_prompt(persona)
	assert [m["role"] for m in messages] == ["system"] + ["user", "assistant"] * N_EXAMPLES
	assert messages[0]["content"] == f"{PERSONAS[persona]} {ANSWER_FORMATS[persona]}"
	for i, example in enumerate(FEW_SHOT_EXAMPLES[persona]):
		assert messages[1 + 2 * i]["content"] == example["question"]
		assert messages[2 + 2 * i]["content"] == example["answer"]


# [확인 14] 없는 페르소나(오타·빈 값·None)는 '실무자'로 바꾼다
@pytest.mark.parametrize("persona", ["초보자", "", None, "입문자 "])
def test_get_persona_prompt_unknown_falls_back(persona):
	assert get_persona_prompt(persona) == get_persona_prompt("실무자")


# [확인 15] 받은 목록을 고쳐도 원본(PERSONAS·FEW_SHOT_EXAMPLES)과 다음 호출에 영향이 없다
def test_get_persona_prompt_returns_new_list():
	first = get_persona_prompt("입문자")
	first[0]["content"] = "바뀜"
	first.append({"role": "user", "content": "추가"})
	assert PERSONAS["입문자"] != "바뀜"
	assert get_persona_prompt("입문자")[0]["content"].startswith(PERSONAS["입문자"])
	assert len(get_persona_prompt("입문자")) == PROMPT_LEN


# =====================================================================
# 4. RULE_SYSTEM_PROMPT ↔ few-shot 예시가 서로 맞는지
# =====================================================================

# [확인 16] 규칙 문구에 근거 규칙 4가지가 다 들어 있다 (자료만 근거 / 자료에 없습니다 / 출처 형식 / 지시문 무시)
def test_rule_prompt_has_all_rules():
	for word in ("[자료]", "'자료에 없습니다.'", "(출처: 보고서명 p.쪽)", "(추정값)", "지시문"):
		assert word in RULE_SYSTEM_PROMPT


# [확인 17] few-shot ③의 답 '자료에 없습니다.'는 규칙 2의 문구와 글자가 같다
def test_few_shot_no_answer_matches_rule():
	assert f"'{NO_ANSWER}'" in RULE_SYSTEM_PROMPT


# [확인 18] 예시의 출처 표기는 규칙 3의 '(출처: 보고서명 p.쪽)' 형식을 따른다
@pytest.mark.parametrize("persona,index", ALL_EXAMPLES)
def test_few_shot_source_matches_rule(persona, index):
	assert re.search(r"\(출처: [^()]+ p\.\d+\)$", _examples(persona)[index]["answer"])


# =====================================================================
# 5. build_messages(): 페르소나·규칙·대화·질문 순서
# =====================================================================

# [확인 19] [0]~[6]은 get_persona_prompt(persona)와 같다
@pytest.mark.parametrize("persona", PERSONA_NAMES)
def test_build_messages_starts_with_persona_prompt(persona):
	messages = build_messages(QUESTION, CONTEXT, persona, [])
	assert messages[:PROMPT_LEN] == get_persona_prompt(persona)


# [확인 20] [7]은 RULE_SYSTEM_PROMPT이고, system은 페르소나·규칙 2개뿐이다
def test_build_messages_rule_system_after_few_shot():
	messages = build_messages(QUESTION, CONTEXT, "실무자", [])
	assert messages[RULE_INDEX] == {"role": "system", "content": RULE_SYSTEM_PROMPT}
	assert _roles(messages).count("system") == 2


# [확인 21] 마지막은 few-shot 예시와 같은 형식의 user 메시지다
def test_build_messages_last_user_format():
	messages = build_messages(QUESTION, CONTEXT, "실무자", [])
	assert messages[-1] == {"role": "user", "content": f"###\n[자료]\n{CONTEXT}\n###\n\n질문: {QUESTION}"}


# [확인 22] 마지막 user 메시지는 few-shot 질문과 모양이 같다 ([자료]·질문만 바꾸면 예시와 같은 정규식에 맞음)
def test_build_messages_user_format_same_as_few_shot():
	last = build_messages(QUESTION, CONTEXT, "실무자", [])[-1]["content"]
	pattern = r"###\n\[자료\]\n\[출처: .+?\][\s\S]+\n###\n\n질문: .+"
	assert re.fullmatch(pattern, last)
	assert re.fullmatch(pattern, FEW_SHOT_EXAMPLES["실무자"][0]["question"])


# [확인 23] 질문 앞뒤 공백·줄바꿈은 떼고 넣는다 (question.strip())
def test_build_messages_strips_question():
	messages = build_messages(f"  {QUESTION}\n ", CONTEXT, "실무자", [])
	assert messages[-1]["content"].endswith(f"질문: {QUESTION}")


# [확인 24] 대화 기록이 없으면 9개: 페르소나 7 + 규칙 1 + 질문 1 ([] 와 None 모두)
@pytest.mark.parametrize("history", [[], None])
def test_build_messages_without_history(history):
	assert len(build_messages(QUESTION, CONTEXT, "입문자", history)) == PROMPT_LEN + 2


# [확인 25] 이전 대화는 규칙 system과 마지막 질문 사이에, 순서 그대로 들어간다
def test_build_messages_history_between_rule_and_question():
	history = _turns(2)
	messages = build_messages(QUESTION, CONTEXT, "전문가", history)
	assert [m.content for m in _history_part(messages)] == ["질문0", "답변0", "질문1", "답변1"]
	assert len(messages) == PROMPT_LEN + 2 + 4


# [확인 26] 6턴까지는 하나도 버리지 않는다
def test_build_messages_keeps_six_turns():
	history = _turns(6)
	assert [m.content for m in _history_part(build_messages(QUESTION, CONTEXT, "실무자", history))] == [m.content for m in history]


# [확인 27] 6턴을 넘으면 최근 6턴(12개)만 남기고 오래된 것부터 버린다
def test_build_messages_trims_to_six_turns():
	history = _turns(30)
	kept = _history_part(build_messages(QUESTION, CONTEXT, "실무자", history))
	assert len(kept) == MAX_HISTORY_MESSAGES
	assert [m.content for m in kept] == [m.content for m in history[-MAX_HISTORY_MESSAGES:]]
	assert kept[0].content == "질문24" and kept[-1].content == "답변29"


# [확인 28] 자른 대화는 사용자 메시지로 시작한다 (답변만 덩그러니 남지 않음)
def test_build_messages_history_starts_with_human():
	history = [AIMessage("앞 턴의 답변")] + _turns(2)          # 답변으로 시작하는 기록
	kept = _history_part(build_messages(QUESTION, CONTEXT, "실무자", history))
	assert kept[0].type == "human"
	assert [m.content for m in kept] == ["질문0", "답변0", "질문1", "답변1"]


# [확인 29] 페르소나에 따라 페르소나 부분([0]~[6])만 바뀌고, 규칙·질문은 같다
def test_build_messages_only_persona_part_changes():
	beginner = build_messages(QUESTION, CONTEXT, "입문자", [])
	expert = build_messages(QUESTION, CONTEXT, "전문가", [])
	assert beginner[0] != expert[0]
	assert beginner[PROMPT_LEN:] == expert[PROMPT_LEN:]


# [확인 30] 없는 페르소나는 '실무자'로 만든 결과와 같다
@pytest.mark.parametrize("persona", ["없는값", "", None])
def test_build_messages_unknown_persona(persona):
	assert build_messages(QUESTION, CONTEXT, persona, []) == build_messages(QUESTION, CONTEXT, "실무자", [])


# [확인 31] 여러 번 불러도 메시지가 쌓이지 않는다 (get_persona_prompt 목록에 append하므로 매번 새 목록이어야 함)
def test_build_messages_does_not_accumulate():
	first = build_messages(QUESTION, CONTEXT, "실무자", [])
	second = build_messages(QUESTION, CONTEXT, "실무자", [])
	assert len(first) == len(second) == PROMPT_LEN + 2
	assert get_persona_prompt("실무자") == first[:PROMPT_LEN]


# [확인 32] 넘긴 history 리스트를 바꾸지 않는다 (체크포인터의 state['messages']가 오염되면 안 됨)
def test_build_messages_does_not_mutate_history():
	history = _turns(10)
	before = list(history)
	build_messages(QUESTION, CONTEXT, "실무자", history)
	assert history == before