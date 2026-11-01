"""질의응답 처리 · ask_stream (장유빈) / validate_question, mask_pii (서동안)"""

import logging
import re
from collections.abc import Iterator
from datetime import datetime

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.messages import AIMessageChunk

from hri.domain.schemas import AnswerResult, QALog
from hri.exception.errors import InvalidInputError

logger = logging.getLogger(__name__)

MAX_QUESTION_LENGTH = 1000
# validate_question, mask_pii (서동안)
# 회원가입 정보(이름·아이디·비밀번호)만 가린다. 질문 안에서 라벨('이름:', '아이디는', '비번')
# 바로 뒤에 오는 값을 찾으며, 그룹 1은 라벨·구분 부분, 그룹 2는 가릴 값이다.
_SEP = r"\s*(?:(?:는|은|가|이)(?=[\s:=]))?\s*[:=]?\s*"  # 조사는 뒤에 공백이 있을 때만(이몽·가영 같은 이름 보호)
NAME_RE = re.compile(
    rf"((?:이름|성명){_SEP})(?!뭐|무엇|뭔|어떻)([가-힣]{{2,4}}?)"
	r"(?=입니다|이고|이에요|예요|이야|인데|이며|이다|이요|이라|(?![가-힣]))"
)
ID_RE = re.compile(rf"((?<![A-Za-z])(?:아이디|ID){_SEP})([A-Za-z0-9_.\-]{{3,}})", re.IGNORECASE)
PASSWORD_RE = re.compile(
    rf"((?<![A-Za-z])(?:비밀번호|비번|패스워드|암호|password|passwd|pwd|pw){_SEP})([!-~]+)",
    re.IGNORECASE,
)


def validate_question(question: str) -> str:
	"""빈 입력·공백만 입력·최대 길이 초과면 InvalidInputError. 통과하면 앞뒤 공백을 뗀 질문을 반환한다."""
	question = question.strip()
	if not question:
		raise InvalidInputError("질문을 입력해 주세요.")
	if len(question) > MAX_QUESTION_LENGTH:
		raise InvalidInputError(f"질문은 {MAX_QUESTION_LENGTH:,}자 이하로 입력해 주세요.")
	return question


def mask_pii(text: str) -> str:
	"""이름(홍*동)·아이디(abc***)·비밀번호(********)를 가린다.

	LLM 호출 전과 qa_logs 저장 전에 사용한다. '이름: 홍길동'처럼 라벨이 붙은 값만 찾는다.
	"""
	text = NAME_RE.sub(lambda m: m.group(1) + m.group(2)[0] + "*" * (len(m.group(2)) - 2) + m.group(2)[-1]
						if len(m.group(2)) > 2 else m.group(1) + m.group(2)[0] + "*", text)
	text = ID_RE.sub(
		lambda m: m.group(1) + m.group(2)[:3 if len(m.group(2)) > 3 else 1]
		+ "*" * (len(m.group(2)) - (3 if len(m.group(2)) > 3 else 1)),
		text,
	)
	return PASSWORD_RE.sub(lambda m: m.group(1) + "********", text)

# =====================================================================
# [장유빈] 질의응답 처리 · ask_stream (WBS 3.3.8)
# qa_graph를 실행해 답변 글자를 실시간으로 내보내고, 마지막에 AnswerResult를 넘긴다
# =====================================================================

STREAM_NODES = ("generate", "chat")  # LLM이 답변을 만드는 노드: 글자 조각을 그대로 화면에 내보낸다
ANSWER_NODES = ("generate", "chat", "no_answer", "error_reply")  # 최종 답변을 만드는 노드 4개
RESULT_KEYS = ("question", "answer", "route", "has_source", "sources", "suggestions", "log_id")  # 노드가 돌려준 값 중 결과에 쓰는 것
VALID_PERSONAS = ("입문자", "실무자", "전문가")
ERROR_ANSWER = "답변을 만드는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."


def _get_graph():
	"""qa_graph를 레지스트리에서 받는다.

	호출하는 시점에 불러온다: 레지스트리가 그래프 모듈(DB·LLM·설정 .env 필요)을 불러오므로,
	이 파일을 import하는 것만으로는 그것들이 필요해지지 않게 하고 테스트에서 이 함수만 바꿔 끼울 수 있게 한다.
	"""
	from hri.graph.registry import get_qa_graph

	return get_qa_graph()


def _turn_inputs(
	question: str, persona: str, report_ids: list[int] | None, k: int, use_mmr: bool, user_id: str
) -> dict:
	"""그래프에 넣을 이번 질문 입력을 만든다.

	체크포인터가 이전 턴 값을 thread별로 남겨 두므로(messages만 누적, 나머지는 덮어쓰기),
	이번 질문 전용 필드를 매번 비워서 넣는다. messages는 넣지 않아야 이전 대화가 이어진다.
	"""
	return {
		"question": question,
		"persona": persona,
		"report_ids": report_ids or [],
		"k": k,
		"use_mmr": use_mmr,
		"user_id": user_id,
		"error": None,
		"docs": [],
		"context_docs": [],
		"sources": [],
		"suggestions": [],
		"terms": [],
		"answer": "",
		"started_at": datetime.now(),
	}


def _chunk_text(message) -> str:
	"""stream_mode='messages'로 온 메시지에서 LLM이 방금 만든 글자 조각만 꺼낸다. 그 밖의 메시지는 빈 문자열.

	이 모드에는 글자 조각(AIMessageChunk) 말고도, 노드가 돌려준 질문(HumanMessage)과 완성된 답변(AIMessage)이
	같은 노드 이름으로 한 번 더 온다. 그대로 내보내면 질문과 답이 중복으로 보이므로 조각만 받는다.
	"""
	if isinstance(message, AIMessageChunk) and isinstance(message.content, str):
		return message.content
	return ""


def _count_tokens(usage: dict) -> int:
	"""모델별 사용량({'모델명': {'input_tokens', 'output_tokens', 'total_tokens'}})의 토큰 수를 모두 더한다."""
	return sum(
		tokens.get("total_tokens", tokens.get("input_tokens", 0) + tokens.get("output_tokens", 0))
		for tokens in usage.values()
	)


def _save_error_log(
	question: str, persona: str, session_id: str, user_id: str, started_at: datetime, error: Exception
) -> int | None:
	"""그래프 밖에서 난 예외를 qa_logs에 route='error'로 저장하고 log_id를 돌려준다. 저장도 실패하면 None.

	저장 실패가 원래 오류 안내를 막지 않도록 여기서 예외를 삼키고 기록만 남긴다.
	"""
	try:
		from hri.service.history_service import save_qa_log

		return save_qa_log(QALog(
			user_id=user_id,
			session_id=session_id,
			question=mask_pii(question),
			answer=ERROR_ANSWER,
			persona=persona if persona in VALID_PERSONAS else "실무자",
			route="error",
			has_source=False,
			response_ms=max(int((datetime.now() - started_at).total_seconds() * 1000), 0),
			tokens=0,
			cost_krw=0.0,
			error=str(error)[:500],
		))
	except Exception:
		logger.exception("오류 기록 저장 실패")
		return None


def ask_stream(
	question: str,
	persona: str,
	session_id: str,
	user_id: str,
	report_ids: list[int] | None = None,
	k: int = 4,
	use_mmr: bool = False,
) -> Iterator[str | AnswerResult]:
	"""질문 1건을 qa_graph로 실행하며, 답변 글자 조각(str)을 차례로 내보내고 마지막에 AnswerResult를 내보낸다.

	- 그래프는 thread_id=session_id로 실행해 같은 세션의 이전 대화가 이어진다. 토큰·비용 집계용
	  UsageMetadataCallbackHandler를 config['configurable']['usage']와 callbacks에 넣어 save_log 노드가 읽게 한다.
	- stream_mode=['messages', 'updates']: 'messages'로 generate·chat 노드의 글자 조각만 내보낸다.
	  no_answer·error_reply 노드는 LLM이 글자를 만들지 않으므로 'updates'의 answer를 한 번에 내보낸다
	  (generate·chat도 글자 조각이 없었다면 같은 방식으로 answer를 내보낸다).
	- 마지막 AnswerResult의 답변·출처·추천 질문·경로·기록 번호는 'updates'로 받은 값이다.
	  question은 validate 노드가 가린 질문이 없으면 mask_pii()로 직접 가린다.
	  tokens는 사용량의 합이다. 원화 비용은 save_log 노드가 qa_logs에 저장하므로 여기서는 계산하지 않는다(0.0).
	- 그래프 밖에서 예외가 나면 기록을 남기고(_save_error_log) 오류 안내 문구를 담은 AnswerResult(route='error')를 낸다.
	"""
	started_at = datetime.now()
	handler = UsageMetadataCallbackHandler()
	config = {"configurable": {"thread_id": session_id, "usage": handler}, "callbacks": [handler]}
	inputs = _turn_inputs(question, persona, report_ids, k, use_mmr, user_id)
	final: dict = {}  # 'updates'로 받은 이번 질문의 결과 값 모음
	streamed = False  # 글자 조각을 한 번이라도 내보냈는지

	try:
		for mode, payload in _get_graph().stream(inputs, config=config, stream_mode=["messages", "updates"]):
			if mode == "messages":
				message, metadata = payload
				text = _chunk_text(message)
				if text and metadata.get("langgraph_node") in STREAM_NODES:
					streamed = True
					yield text
			elif mode == "updates":
				for node, update in payload.items():
					if not isinstance(update, dict):
						continue
					final.update({key: update[key] for key in RESULT_KEYS if key in update})
					# 글자 조각으로 이미 보낸 답변은 다시 보내지 않는다
					if node in ANSWER_NODES and "answer" in update and not (node in STREAM_NODES and streamed):
						yield update["answer"]
	except Exception as error:
		logger.exception("질의응답 처리 중 오류")
		log_id = final.get("log_id")
		if log_id is None:  # 그래프 안에서 이미 기록했으면 다시 저장하지 않는다
			log_id = _save_error_log(question, persona, session_id, user_id, started_at, error)
		yield AnswerResult(
			question=final.get("question") or mask_pii(question),
			answer=ERROR_ANSWER,
			route="error",
			has_source=False,
			log_id=log_id,
		)
		return

	yield AnswerResult(
		question=final.get("question") or mask_pii(question),
		answer=final.get("answer", ""),
		sources=final.get("sources") or [],
		suggestions=final.get("suggestions") or [],
		has_source=bool(final.get("has_source", False)),
		route=final.get("route", "error"),
		tokens=_count_tokens(dict(handler.usage_metadata)),
		log_id=final.get("log_id"),
	)