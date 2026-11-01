"""rewrite_question 로직 테스트 · 가짜 LLM 사용 (장유빈)"""
from langchain_core.messages import AIMessage, HumanMessage

from hri.memory.history_store import rewrite_question

HISTORY = [HumanMessage(content="내년 GDP 성장률 전망은?"), AIMessage(content="2.1%로 전망합니다.")]


class _FakeLLM:
	"""invoke에 받은 메시지를 기록하고 정해 둔 글을 돌려주는 가짜 LLM."""

	def __init__(self, text: str):
		self.text = text
		self.messages = None

	def invoke(self, messages):
		self.messages = messages
		return AIMessage(content=self.text)


def _patch_llm(monkeypatch, fake: _FakeLLM):
	monkeypatch.setattr("hri.llm.llm_client.get_chat_model", lambda **kwargs: fake)


def _forbid_llm(monkeypatch):
	def boom(**kwargs):
		raise AssertionError("LLM을 부르면 안 됩니다")

	monkeypatch.setattr("hri.llm.llm_client.get_chat_model", boom)


def test_no_history_returns_question(monkeypatch):
	_forbid_llm(monkeypatch)
	assert rewrite_question("그거 물가는?", []) == "그거 물가는?"


def test_empty_question_returns_as_is(monkeypatch):
	_forbid_llm(monkeypatch)
	assert rewrite_question("   ", HISTORY) == "   "


def test_rewrites_with_history(monkeypatch):
	fake = _FakeLLM("  내년 물가 전망은?  ")
	_patch_llm(monkeypatch, fake)
	assert rewrite_question("그거 물가는?", HISTORY) == "내년 물가 전망은?"
	user_content = fake.messages[1][1]
	assert "내년 GDP 성장률 전망은?" in user_content
	assert "그거 물가는?" in user_content


def test_empty_llm_result_falls_back(monkeypatch):
	_patch_llm(monkeypatch, _FakeLLM("   "))
	assert rewrite_question("그거 물가는?", HISTORY) == "그거 물가는?"