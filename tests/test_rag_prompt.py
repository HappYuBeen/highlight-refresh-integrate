"""rag_prompt 테스트 · format_docs, build_messages (장유빈)"""
from langchain_core.messages import AIMessage, HumanMessage, convert_to_messages

from hri.domain.schemas.qa import ContextDoc
from hri.prompt.persona_prompt import get_persona_prompt
from hri.prompt.rag_prompt import RULE_SYSTEM_PROMPT, build_messages, format_docs


def _doc(**kwargs) -> ContextDoc:
	base = dict(
		report_id=1, report_title="2026 경제전망", kind="text", section_title="물가",
		caption=None, page_start=12, page_end=13, text="본문입니다", needs_review=False,
	)
	base.update(kwargs)
	return ContextDoc(**base)


def test_text_doc():
	assert format_docs([_doc()]) == "[출처: 2026 경제전망 p.12-13 / 물가]\n본문입니다"


def test_no_section_title():
	assert format_docs([_doc(section_title=None)]) == "[출처: 2026 경제전망 p.12-13]\n본문입니다"


def test_chart_doc():
	doc = _doc(kind="chart", caption="그림 3", page_start=20, page_end=20)
	assert format_docs([doc]) == "[출처: 2026 경제전망 p.20 / 그림 3 (차트 OCR)]\n본문입니다"


def test_needs_review():
	doc = _doc(kind="chart", caption="그림 3", page_start=20, page_end=20, needs_review=True)
	assert format_docs([doc]).startswith("[출처: 2026 경제전망 p.20 / 그림 3 (차트 OCR)] (추정값)\n")


def test_multiple_and_empty():
	text = format_docs([_doc(text="A"), _doc(text="B")])
	assert text == "[출처: 2026 경제전망 p.12-13 / 물가]\nA\n\n[출처: 2026 경제전망 p.12-13 / 물가]\nB"
	assert format_docs([]) == ""


# ---------------------------------------------------------------------
# build_messages 테스트
# ---------------------------------------------------------------------

def _role(message) -> str:
	"""dict 메시지는 role, LangChain 메시지는 type으로 역할을 돌려준다 ('human' → 'user' 등으로 통일)"""
	role = message["role"] if isinstance(message, dict) else message.type
	return {"human": "user", "ai": "assistant"}.get(role, role)


def _history(turns: int) -> list:
	"""질문·답변 turns쌍의 이전 대화"""
	history = []
	for i in range(turns):
		history += [HumanMessage(f"질문{i}"), AIMessage(f"답변{i}")]
	return history


def test_build_messages_order_without_history():
	messages = build_messages("GDP 전망은?", "[출처: A p.1]\n본문", "실무자", [])
	few_shot = get_persona_prompt("실무자")
	assert messages[:len(few_shot)] == few_shot  # 맨 앞은 페르소나 system + few-shot
	assert messages[len(few_shot)] == {"role": "system", "content": RULE_SYSTEM_PROMPT}
	assert [_role(m) for m in messages[len(few_shot) + 1:]] == ["user"]


def test_build_messages_user_message_format():
	messages = build_messages("  GDP 전망은?  ", "[출처: A p.1]\n본문", "실무자", [])
	assert messages[-1] == {
		"role": "user",
		"content": "###\n[자료]\n[출처: A p.1]\n본문\n###\n\n질문: GDP 전망은?",
	}


def test_build_messages_user_message_matches_few_shot_format():
	few_shot_question = get_persona_prompt("실무자")[1]["content"]  # 첫 few-shot의 user 메시지
	assert few_shot_question.startswith("###\n[자료]\n")
	assert "\n###\n\n질문: " in few_shot_question
	assert "\n###\n\n질문: " in build_messages("q", "c", "실무자", [])[-1]["content"]


def test_build_messages_history_between_rule_and_question():
	history = _history(2)
	messages = build_messages("후속 질문", "자료", "입문자", history)
	few_shot_len = len(get_persona_prompt("입문자"))
	assert messages[few_shot_len + 1:-1] == history
	assert messages[-1]["role"] == "user" and "질문: 후속 질문" in messages[-1]["content"]


def test_build_messages_history_is_trimmed_to_recent_6_turns():
	messages = build_messages("q", "c", "전문가", _history(10))
	history_part = messages[len(get_persona_prompt("전문가")) + 1:-1]
	assert len(history_part) == 12
	assert history_part[0].content == "질문4"  # 최근 6턴(질문4~질문9)만 남는다
	assert history_part[-1].content == "답변9"


def test_build_messages_unknown_persona_falls_back():
	assert build_messages("q", "c", "없는 페르소나", []) == build_messages("q", "c", "실무자", [])


def test_build_messages_none_history_is_ok():
	messages = build_messages("q", "c", "실무자", None)
	assert _role(messages[-1]) == "user"


def test_build_messages_can_be_sent_to_langchain_model():
	"""dict와 LangChain 메시지가 섞여 있어도 모델 입력 형식으로 바뀐다"""
	messages = build_messages("q", "c", "실무자", _history(1))
	converted = convert_to_messages(messages)
	assert len(converted) == len(messages)
	assert converted[0].type == "system" and converted[-1].type == "human"

# ---------------------------------------------------------------------
# build_messages 테스트
# ---------------------------------------------------------------------

def _role(message) -> str:
	"""dict 메시지는 role, LangChain 메시지는 type으로 역할을 돌려준다 ('human' → 'user' 등으로 통일)"""
	role = message["role"] if isinstance(message, dict) else message.type
	return {"human": "user", "ai": "assistant"}.get(role, role)


def _history(turns: int) -> list:
	"""질문·답변 turns쌍의 이전 대화"""
	history = []
	for i in range(turns):
		history += [HumanMessage(f"질문{i}"), AIMessage(f"답변{i}")]
	return history


def test_build_messages_order_without_history():
	messages = build_messages("GDP 전망은?", "[출처: A p.1]\n본문", "실무자", [])
	few_shot = get_persona_prompt("실무자")
	assert messages[:len(few_shot)] == few_shot  # 맨 앞은 페르소나 system + few-shot
	assert messages[len(few_shot)] == {"role": "system", "content": RULE_SYSTEM_PROMPT}
	assert [_role(m) for m in messages[len(few_shot) + 1:]] == ["user"]


def test_build_messages_user_message_format():
	messages = build_messages("  GDP 전망은?  ", "[출처: A p.1]\n본문", "실무자", [])
	assert messages[-1] == {
		"role": "user",
		"content": "###\n[자료]\n[출처: A p.1]\n본문\n###\n\n질문: GDP 전망은?",
	}


def test_build_messages_user_message_matches_few_shot_format():
	few_shot_question = get_persona_prompt("실무자")[1]["content"]  # 첫 few-shot의 user 메시지
	assert few_shot_question.startswith("###\n[자료]\n")
	assert "\n###\n\n질문: " in few_shot_question
	assert "\n###\n\n질문: " in build_messages("q", "c", "실무자", [])[-1]["content"]


def test_build_messages_history_between_rule_and_question():
	history = _history(2)
	messages = build_messages("후속 질문", "자료", "입문자", history)
	few_shot_len = len(get_persona_prompt("입문자"))
	assert messages[few_shot_len + 1:-1] == history
	assert messages[-1]["role"] == "user" and "질문: 후속 질문" in messages[-1]["content"]


def test_build_messages_history_is_trimmed_to_recent_6_turns():
	messages = build_messages("q", "c", "전문가", _history(10))
	history_part = messages[len(get_persona_prompt("전문가")) + 1:-1]
	assert len(history_part) == 12
	assert history_part[0].content == "질문4"  # 최근 6턴(질문4~질문9)만 남는다
	assert history_part[-1].content == "답변9"


def test_build_messages_unknown_persona_falls_back():
	assert build_messages("q", "c", "없는 페르소나", []) == build_messages("q", "c", "실무자", [])


def test_build_messages_none_history_is_ok():
	messages = build_messages("q", "c", "실무자", None)
	assert _role(messages[-1]) == "user"


def test_build_messages_can_be_sent_to_langchain_model():
	"""dict와 LangChain 메시지가 섞여 있어도 모델 입력 형식으로 바뀐다"""
	messages = build_messages("q", "c", "실무자", _history(1))
	converted = convert_to_messages(messages)
	assert len(converted) == len(messages)
	assert converted[0].type == "system" and converted[-1].type == "human"