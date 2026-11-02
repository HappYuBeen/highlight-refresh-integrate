"""
개인정보 마스킹 테스트 (서동안, WBS 4.1.3)
질문에 회원가입 정보(이름·아이디·비밀번호)를 적었을 때 mask_pii()가 가리는지,
save_qa_log()가 가린 값으로 qa_logs에 저장하는지 확인한다 (DB 없이 실행).

회원가입(user_service.register)은 아이디·비밀번호·이름만 받는 단순한 구조라,
'이름: 홍길동'처럼 라벨이 붙은 기본 표현만 확인한다.
	- 이름: 홍길동 → 홍*동
	- 아이디: abc123 → abc***
	- 비밀번호: 길이와 상관없이 ********
"""

import pytest

from hri.domain.schemas import QALog
from hri.service.qa_service import mask_pii


# [확인 1] 이름은 첫·끝 글자만 남긴다
@pytest.mark.parametrize("text,expected", [
	("이름: 홍길동", "이름: 홍*동"),
	("이름은 홍길동입니다", "이름은 홍*동입니다"),
	("이름: 김철", "이름: 김*"),
])
def test_name_masked(text, expected):
	assert mask_pii(text) == expected


# [확인 2] 아이디는 앞 3글자만 남긴다
@pytest.mark.parametrize("text,expected", [
	("아이디: abc123", "아이디: abc***"),
	("아이디는 hong123", "아이디는 hon****"),
	("ID: user01", "ID: use***"),
])
def test_id_masked(text, expected):
	assert mask_pii(text) == expected


# [확인 3] 비밀번호는 길이와 상관없이 ******** 8개로 바꾼다
@pytest.mark.parametrize("text,expected", [
	("비밀번호: qwer1234!", "비밀번호: ********"),
	("비번은 1234", "비번은 ********"),
	("password: abc123", "password: ********"),
])
def test_password_masked(text, expected):
	assert mask_pii(text) == expected


# [확인 4] 세 가지가 한 문장에 함께 있어도 모두 가린다
def test_all_three_together():
	text = "이름: 홍길동, 아이디: hong123, 비밀번호: pw1234!"
	assert mask_pii(text) == "이름: 홍*동, 아이디: hon****, 비밀번호: ********"


# [확인 5] 회원가입 정보가 없는 질문은 바꾸지 않는다
@pytest.mark.parametrize("text", [
	"2026년 하반기 소비자물가 상승률은 어느 범위로 전망되나요?",
	"비밀번호를 잊어버렸어요",
	"이름이 뭐야?",
])
def test_plain_text_unchanged(text):
	assert mask_pii(text) == text


def _log(question: str, answer: str) -> QALog:
	return QALog(user_id="tester", session_id="s-1", question=question, answer=answer, persona="실무자",
		route="chat", has_source=False, response_ms=100, tokens=10, cost_krw=0.1)


# [확인 6] qa_logs 저장값: save_qa_log는 가린 질문·답변을 insert_qa_log에 넘긴다
def test_save_qa_log_masks_before_insert(monkeypatch):
	import hri.service.history_service as history_service

	saved = []
	monkeypatch.setattr(history_service, "insert_qa_log", lambda log: saved.append(log) or 7)

	log_id = history_service.save_qa_log(_log(
		"이름: 홍길동, 아이디: hong123, 비밀번호: pw1234! 로 가입했는데 로그인이 안 돼요",
		"아이디: hong123 으로 다시 시도해 보세요",
	))

	assert log_id == 7
	assert saved[0].question == "이름: 홍*동, 아이디: hon****, 비밀번호: ******** 로 가입했는데 로그인이 안 돼요"
	assert saved[0].answer == "아이디: hon**** 으로 다시 시도해 보세요"
	assert (saved[0].route, saved[0].tokens) == ("chat", 10)  # 가리는 것 말고는 그대로