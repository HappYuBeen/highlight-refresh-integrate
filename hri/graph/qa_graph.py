"""질의응답 흐름 · build_qa_graph, route_question, route_after_grade, generate_node, no_answer_node, error_reply_node, chat_node, save_log_node (엄정현) / validate_node, rewrite_node, retrieve_node, sources_node (류현수)

[3.8.4] 그래프 구성·분기(build_qa_graph·route_question·route_after_grade)와 체크포인터 연결을 명세대로 채웠다.
[3.8.5] 답을 만드는 노드(generate·no_answer·error_reply·chat)와 기록 노드(save_log)를 채웠다.
	- 답을 만드는 노드 4개는 모두 같은 모양으로 돌려준다:
	  {'answer': 답변, 'has_source': 근거 유무, 'route': 최종 경로, 'messages': [이번 질문, 이번 답변]}
	  messages는 add_messages 리듀서로 체크포인터의 대화 기록 끝에 붙는다 (다음 질문 때 이전 대화로 쓰임).
	- save_log는 어느 갈래로 끝나든 마지막에 한 번 실행되어 질문 기록(qa_logs)과 LLM 사용량(llm_usage)을 저장한다.
검색 흐름 노드(validate·rewrite·retrieve·sources)를 채웠다.
	- validate: validate_question·mask_pii (서동안 qa_service)
	- rewrite: rewrite_question (장유빈 history_store)
	- retrieve: hybrid_search (장유빈 search)
	- sources: format_sources (장유빈 source_formatter)
grade는 분기 함수 route_after_grade가 has_context()로 판단하므로 가짜({} 반환) 노드로 둔다.
suggest는 후속 추천 질문 제거 전까지 가짜 노드로 둔다.

흐름 (LangGraph 워크플로우 명세서 build_qa_graph 행)
	START → validate → route_question
		├─ 'error'   → error_reply ─────────────────────────────┐
		├─ 'chat'    → chat ────────────────────────────────────┤
		└─ 'rewrite' → rewrite → retrieve → grade → route_after_grade
				├─ 'no_answer' → no_answer ─────────────────────┤
				└─ 'generate'  → generate → [sources ∥ suggest] ┴→ save_log → END
compile(checkpointer=get_checkpointer())은 registry.get_qa_graph()가 한 번만 한다 (thread_id = session_id).
"""

import logging
import re
from datetime import datetime
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from hri.domain.schemas import LlmUsage, QALog, QAState, RouteDecision
from hri.prompt.rag_prompt import has_context

logger = logging.getLogger(__name__)

# 인사·감사·잡담 정규식 (명세서: '인사·감사·잡담 정규식(GREETING_RE)에 걸리면 chat')
# 질문 '전체'가 아래 표현(여러 개 이어져도 됨)과 문장부호로만 되어 있을 때만 'chat'이다.
# 문장 일부에만 들어 있으면 맞지 않는다 → '날씨가 경제에 미치는 영향은?', '음, 금리 전망은?'은 3단계로 간다.
_SEP = r"[\s,!.~?^ㅠㅜ]"  # 표현 사이·끝에 올 수 있는 공백·문장부호

# ① 인사·감사
_GREETING_WORDS = (
	r"안녕(?:하세요|하십니까|히\s*(?:계세요|가세요))?|반가워요?|반갑습니다|고마워요?|고맙습니다"
	r"|감사(?:합니다|해요|드립니다)?|수고(?:하세요|하셨습니다|했어요|하셨어요)?|잘\s*부탁(?:해요|드립니다)"
	r"|hi|hello|hey|thanks?|thank\s*you"
)
# ② 웃음·감탄만 있는 말: ㅋㅋ, ㅎㅎ, 음..., 오, 와
_REACTION_WORDS = r"[ㅋㅎ]+|음+|오+|와+"
# ③ 챗봇에 대한 질문: 너는 누구야, 뭐 할 수 있어, 이 서비스 기능
_BOT_WORDS = (
	r"(?:너는?|넌)?\s*누구(?:야|니|세요|예요|인가요)?"
	r"|(?:너는?|넌)?\s*(?:뭐|뭘|무엇을)\s*할\s*수\s*있(?:어|어요|나요|니)?"
	r"|(?:이\s*)?서비스(?:는|의)?\s*(?:무슨\s*|어떤\s*)?기능(?:이|은)?(?:\s*(?:있어|있어요|있나요|뭐야|뭐예요|알려\s*줘|알려\s*주세요))?"
)
# ④ 일상 잡담: 날씨, 배고프다, 심심해, 잘 지내?
_SMALLTALK_WORDS = (
	r"(?:오늘\s*)?날씨(?:가|는)?(?:\s*(?:어때|어때요|좋다|좋네|좋네요|좋아요))?"
	r"|배고프다|배고파(?:요)?|심심해(?:요)?|심심하다"
	r"|잘\s*지내(?:요|니|셨어요|세요)?"
)
_CHAT_WORDS = "|".join([_GREETING_WORDS, _REACTION_WORDS, _BOT_WORDS, _SMALLTALK_WORDS])
GREETING_RE = re.compile(
	rf"^\s*(?:{_CHAT_WORDS})(?:{_SEP}*(?:{_CHAT_WORDS}))*{_SEP}*$",
	re.IGNORECASE,
)

# 정규식·키워드로 정하지 못한 질문을 LLM이 분류할 때 쓰는 지시문 (RouteDecision.route의 description과 함께 전달됨)
ROUTE_SYSTEM_PROMPT = (
	"너는 경제 분석 보고서 질의응답 서비스의 질문 분류기다. "
	"사용자의 말이 보고서 내용을 찾아 답해야 하는 질문이면 'rewrite', "
	"인사·감사·잡담처럼 보고서 검색이 필요 없으면 'chat'을 고른다."
)


# 근거를 찾지 못했을 때의 답변 (명세서 no_answer_node 행·서비스 기능 정의서 '근거 없음 응답': '자료에 없습니다')
NO_ANSWER_TEXT = "자료에 없습니다"


def _skeleton_node(state: QAState) -> dict:
	"""뼈대용 가짜 노드: state를 바꾸지 않는다. 함수를 연결하지 않는 노드(grade·suggest)가 쓴다."""
	return {}


def _reply(state: QAState, answer: str, route: str, has_source: bool) -> dict:
	"""답을 만드는 노드 4개가 공통으로 돌려주는 state 갱신분을 만든다.

	- answer: 화면에 보여 줄 답변
	- has_source: 보고서 근거로 답했는지 (generate만 True)
	- route: 이번 답변의 최종 경로 ('rag' | 'no_answer' | 'chat' | 'error', qa_logs.route와 같은 값)
	- messages: [HumanMessage(이번 질문), AIMessage(이번 답변)]
	  QAState.messages는 add_messages 리듀서라 기존 대화 기록 뒤에 이어 붙는다.
	"""
	return {
		"answer": answer,
		"has_source": has_source,
		"route": route,
		"messages": [HumanMessage(state["question"]), AIMessage(answer)],
	}

def validate_node(state: QAState) -> dict:
	"""[검증 노드] 질문을 검사하고, 통과하면 개인정보를 가린 질문으로 바꾼다.

	1. validate_question(질문): 빈 입력·공백만·최대 길이 초과면 InvalidInputError (서동안 qa_service)
	   → {'error': 안내 문구}. route_question이 error_reply로 보낸다.
	2. 통과하면 mask_pii(앞뒤 공백을 뗀 질문) → {'question': 가린 질문, 'error': None}
	   이후 노드(rewrite·retrieve·generate·save_log)는 가린 질문을 쓴다.
	qa_service는 함수 안에서 import한다 (그래프 파일 import 때 서비스 모듈을 불러오지 않으려고).
	"""
	from hri.exception.errors import InvalidInputError
	from hri.service.qa_service import mask_pii, validate_question

	try:
		question = validate_question(state.get("question") or "")
	except InvalidInputError as error:
		return {"error": str(error)}
	return {"question": mask_pii(question), "error": None}


def rewrite_node(state: QAState) -> dict:
	"""[다시 쓰기 노드] 이전 대화를 보고 후속 질문을 혼자서도 뜻이 통하는 질문으로 바꾼다.

	rewrite_question(질문, state['messages'])를 부른다 (장유빈 history_store).
	state['messages']는 체크포인터에 쌓인 지난 질문·답변이고 이번 질문은 아직 들어 있지 않다.
	이전 대화가 없으면 rewrite_question이 LLM 없이 질문을 그대로 돌려준다.
	돌려주는 값: {'rewritten': 다시 쓴 질문}
	"""
	from hri.memory.history_store import rewrite_question

	return {"rewritten": rewrite_question(state["question"], state.get("messages") or [])}


def retrieve_node(state: QAState) -> dict:
	"""[검색 노드] 다시 쓴 질문으로 선택한 보고서에서 근거 청크를 찾는다.

	hybrid_search(질문, k, report_ids, use_mmr)를 부른다 (장유빈 search).
	- 질문: state['rewritten'] (비어 있으면 state['question'])
	- report_ids가 비어 있으면 None으로 넘겨 전체 보고서에서 찾는다 (보고서를 고르지 않은 경우)
	돌려주는 값: {'docs': 찾은 청크 목록} (없으면 빈 목록 → route_after_grade가 no_answer로 보낸다)
	"""
	from hri.retriever.search import hybrid_search

	question = state.get("rewritten") or state["question"]
	docs = hybrid_search(
		question,
		k=state["k"],
		report_ids=state.get("report_ids") or None,
		use_mmr=state["use_mmr"],
	)
	return {"docs": docs}


def generate_node(state: QAState) -> dict:
	"""[답변 노드] 검색된 근거로 LLM 답변을 만든다 (근거가 있을 때만 이 노드로 온다).

	순서 (명세서 generate_node 행)
	1. get_context_docs(state['docs'])  : 검색된 청크를 답변 근거로 바꾼다
	   (텍스트 청크 → 그 청크가 속한 섹션 원문, 표·차트 청크 → 원본 표. 김희진 report_dao)
	2. format_docs(context_docs)        : 근거를 '[출처: 보고서명 p.쪽] 본문' 모양의 [자료] 글로 만든다 (장유빈 rag_prompt)
	3. build_messages(질문, [자료], 페르소나, 이전 대화) : LLM에 보낼 메시지 목록을 만든다 (장유빈 rag_prompt)
	   - 이전 대화는 state['messages'](체크포인터에 쌓인 지난 질문·답변)이다. 이번 질문은 아직 들어 있지 않다.
	4. get_chat_model().invoke(messages) : LLM이 답변을 만든다
	   - 이 노드 안에서 부르므로 ask_stream()의 stream_mode='messages'로 글자가 실시간으로 화면에 전달된다.

	돌려주는 값: {'answer', 'has_source': True, 'route': 'rag', 'context_docs', 'messages'}
	함수들은 함수 안에서 import한다 (DB·LLM 모듈을 그래프 파일 import 때 불러오지 않으려고).
	"""
	from hri.dao.report_dao import get_context_docs
	from hri.llm.llm_client import get_chat_model
	from hri.prompt.rag_prompt import build_messages, format_docs

	context_docs = get_context_docs(state["docs"])
	context = format_docs(context_docs)
	messages = build_messages(state["question"], context, state["persona"], state.get("messages") or [])
	response = get_chat_model().invoke(messages)

	update = _reply(state, response.content, route="rag", has_source=True)
	update["context_docs"] = context_docs
	return update


def sources_node(state: QAState) -> dict:
	"""[출처 노드] generate가 만든 답변 근거(context_docs)로 화면에 보여 줄 출처 목록을 만든다.

	format_sources(state['context_docs'])를 부른다 (장유빈 source_formatter).
	돌려주는 값: {'sources': [{report_id, report_title, page_start, page_end, kind, label, needs_review}, ...]}
	"""
	from hri.source.source_formatter import format_sources

	return {"sources": format_sources(state.get("context_docs") or [])}


def no_answer_node(state: QAState) -> dict:
	"""[근거 없음 노드] 검색 결과가 없을 때 LLM을 부르지 않고 '자료에 없습니다'로 답한다.

	근거 없이 LLM이 답을 지어내지 않게 하려는 노드다 (서비스 기능 정의서 '근거 없음 응답').
	돌려주는 값: {'answer': '자료에 없습니다', 'has_source': False, 'route': 'no_answer', 'messages'}
	"""
	return _reply(state, NO_ANSWER_TEXT, route="no_answer", has_source=False)


def error_reply_node(state: QAState) -> dict:
	"""[입력 오류 노드] validate 노드가 적은 안내 문구(state['error'])를 LLM 없이 그대로 답으로 쓴다.

	예) 빈 질문 → '질문을 입력해 주세요.' (validate_question()의 InvalidInputError 문구, 서동안 qa_service)
	state['error']는 그대로 남아 있어서 save_log가 qa_logs.error에도 기록한다 (대시보드 '최근 에러 로그').
	돌려주는 값: {'answer': state['error'], 'has_source': False, 'route': 'error', 'messages'}
	"""
	return _reply(state, state["error"], route="error", has_source=False)


def chat_node(state: QAState) -> dict:
	"""[잡담 노드] 인사·잡담에 chat_bot()으로 짧게 답한다 (검색 없음).

	chat_bot(question)은 서동안 담당(llm_client, WBS 3.7.14)이고, 명세서에 'qa_graph의 chat 노드로 실행'으로 적혀 있다.
	3.8.5 목록에는 없지만, chat 갈래도 save_log에 route='chat'을 넘겨야 질문 기록이 저장되므로 함께 연결했다.
	돌려주는 값: {'answer', 'has_source': False, 'route': 'chat', 'messages'}
	"""
	from hri.llm.llm_client import chat_bot

	return _reply(state, chat_bot(state["question"]), route="chat", has_source=False)


def _total_tokens(usage: dict) -> int:
	"""모델별 사용량({'모델명': {'input_tokens', 'output_tokens', 'total_tokens'}})의 토큰 수를 모두 더한다.

	total_tokens가 없으면 input_tokens + output_tokens로 계산한다.
	"""
	total = 0
	for tokens in usage.values():
		total += tokens.get("total_tokens", tokens.get("input_tokens", 0) + tokens.get("output_tokens", 0))
	return total


def _cost_krw(usage: dict) -> float:
	"""질문 1건의 원화 비용. 사용량이 없으면 환율을 조회하지 않고 0원.

	환율·단가를 못 구해도(.env MODEL_PRICES 누락 등) 질문 기록은 남기려고 로그만 남기고 0원으로 둔다.
	(qa_logs.cost_krw는 NOT NULL DEFAULT 0 이라 None이 아니라 0.0을 돌려준다)
	"""
	if not usage:
		return 0.0

	from hri.llm.llm_client import cost_korean_won, get_usd_krw

	try:
		return cost_korean_won(usage, get_usd_krw())
	except Exception:
		logger.exception("질의응답 비용 계산 실패")
		return 0.0


def save_log_node(state: QAState, config: RunnableConfig) -> dict:
	"""[기록 노드] 질문 1건의 기록(qa_logs)과 LLM 사용량(llm_usage)을 저장하고 {'log_id'}를 돌려준다.

	어느 갈래(rag·no_answer·chat·error)로 끝나든 마지막에 한 번 실행된다
	(generate 갈래는 sources·suggest 두 노드가 모두 끝난 뒤).

	순서 (명세서 save_log_node 행)
	1. 응답 시간: 지금 시각 - state['started_at'](ask_stream이 질문을 받은 시각) → ms
	2. 토큰·비용: ask_stream()이 config['configurable']['usage']로 넘긴 UsageMetadataCallbackHandler의
	   usage_metadata(이번 질문에서 쓴 모델별 토큰 수)를 읽는다.
	   - 원화 비용 = cost_korean_won(usage, get_usd_krw())  (임세준 llm_client)
	   - get_usd_krw()는 토큰 비용용 환율이다. 토큰 비용 환율은 한국수출입은행으로 확정했으므로
	     이 노드는 ECOS 환율 함수를 직접 부르지 않는다.
	   - LLM을 쓰지 않은 질문(근거 없음·입력 오류)은 사용량이 비어 있어 환율을 조회하지 않고 0원으로 둔다.
	   - 환율·단가 오류로 비용 계산이 실패하면 로그만 남기고 0원으로 저장한다 (질문 기록·사용량 저장은 계속).
	3. save_qa_log(QALog)로 qa_logs에 저장하고 log_id를 받는다 (임세준 history_service)
	   - error 갈래면 state['error']가 QALog.error에 들어간다.
	4. 모델마다 insert_usage(LlmUsage(flow='qa', ref_id=log_id, ...))로 llm_usage에 저장한다 (임세준 usage_dao)
	   - LlmUsage 한 줄에 모델 이름이 하나라서 모델별로 한 줄씩 저장한다.

	config는 LangGraph가 노드에 넘겨주는 실행 설정이다. session_id는 config['configurable']['thread_id']에서 읽는다.
	"""
	from hri.dao.usage_dao import insert_usage
	from hri.service.history_service import save_qa_log

	configurable = config.get("configurable", {})

	# 1) 응답 시간 (ms). TODO(확인 필요): started_at이 없을 때(ask_stream이 넣지 않은 경우) 0으로 기록한다.
	started_at = state.get("started_at")
	response_ms = int((datetime.now() - started_at).total_seconds() * 1000) if started_at else 0

	# 2) 토큰·원화 비용. TODO(확인 필요): usage 핸들러가 없으면(ask_stream이 넘기지 않은 경우) 사용량 0으로 기록한다.
	handler = configurable.get("usage")
	usage = dict(handler.usage_metadata) if handler is not None else {}
	cost_krw = _cost_krw(usage)

	# 3) 질문 기록 저장
	log = QALog(
		user_id=state["user_id"],
		session_id=configurable["thread_id"],
		question=state["question"],
		answer=state["answer"],
		persona=state["persona"],
		route=state["route"],
		has_source=state["has_source"],
		response_ms=max(response_ms, 0),
		tokens=_total_tokens(usage),
		cost_krw=cost_krw,
		error=state.get("error"),
	)
	log_id = save_qa_log(log)

	# 4) 모델별 LLM 사용량 저장
	for model, tokens in usage.items():
		try:
			insert_usage(LlmUsage(
				flow="qa",
				user_id=state["user_id"],
				ref_id=log_id,
				model=model,
				input_tokens=tokens.get("input_tokens", 0),
				output_tokens=tokens.get("output_tokens", 0),
			))
		except Exception:
			logger.exception("질의응답 사용량 저장 실패 log_id = %s", log_id)

	return {"log_id": log_id}


def route_question(state: QAState) -> Literal["chat", "rewrite", "error"]:
	"""validate 노드 다음 갈 곳을 고른다 (명세서 순서대로 확인).

	1) state['error']가 있으면(validate 노드에서 InvalidInputError) 'error' → error_reply 노드
	2) 질문 전체가 인사·감사·잡담 표현이면(GREETING_RE) 'chat' → chat 노드 (3단계 extract_keywords 전에 갈라짐)
	3) extract_keywords(질문) 결과가 있으면 'rewrite' → 검색 흐름
	4) 그래도 애매하면(키워드가 하나도 없음. 예: 'ㅇㅇ', '...') get_chat_model(temperature=0).with_structured_output(RouteDecision)으로 분류

	라우팅 함수는 state를 바꾸지 않는다 (반환값으로 갈 노드만 고른다).
	extract_keywords·get_chat_model은 함수 안에서 import한다: 두 모듈은 import할 때 설정(.env)·DB·Chroma 모듈을
	함께 불러오므로, 그래프 파일을 import만 해도 그것들이 필요해지는 것을 막는다.
	"""
	if state.get("error"):
		return "error"

	question = state.get("question") or ""
	# 인사·감사·잡담은 여기서 끝난다 (extract_keywords·LLM을 부르지 않음)
	if GREETING_RE.match(question):
		return "chat"

	from hri.retriever.search import extract_keywords

	if extract_keywords(question):
		return "rewrite"

	from hri.llm.llm_client import get_chat_model

	try:
		classifier = get_chat_model(temperature=0).with_structured_output(RouteDecision)
		decision = classifier.invoke([
			{"role": "system", "content": ROUTE_SYSTEM_PROMPT},
			{"role": "user", "content": question},
		])
		return decision.route if decision else "chat"
	except Exception:
		logger.exception("질문 라우팅 분류 실패 question = %s", question)
		return "chat"

def route_after_grade(state: QAState) -> Literal["generate", "no_answer"]:
	"""grade 다음 갈 곳: has_context(state['docs'])가 True면 'generate', 아니면 'no_answer'.

	has_context()는 검색 결과 청크가 하나라도 있는지만 보는 함수다 (LLM 호출 없음, 장유빈 rag_prompt).
	QAState에 grade 결과를 적는 키가 없으므로 분기 함수가 has_context()를 직접 부른다.
	"""
	if has_context(state.get("docs") or []):
		return "generate"
	return "no_answer"


def build_qa_graph() -> StateGraph:
	"""질의응답 그래프를 노드·엣지까지 연결해 돌려준다 (compile 전 StateGraph)."""
	graph = StateGraph(QAState)

	# 노드 11개
	graph.add_node("validate", validate_node)
	graph.add_node("chat", chat_node)
	graph.add_node("error_reply", error_reply_node)
	graph.add_node("rewrite", rewrite_node)
	graph.add_node("retrieve", retrieve_node)
	graph.add_node("grade", _skeleton_node)
	graph.add_node("no_answer", no_answer_node)
	graph.add_node("generate", generate_node)
	graph.add_node("sources", sources_node)
	graph.add_node("suggest", _skeleton_node)
	graph.add_node("save_log", save_log_node)

	# 검증 → 질문 분류 (route_question의 반환값 → 갈 노드 이름)
	graph.add_edge(START, "validate")
	graph.add_conditional_edges(
		"validate",
		route_question,
		{"chat": "chat", "rewrite": "rewrite", "error": "error_reply"},
	)

	# 보고서 질문: 다시 쓰기 → 검색 → 근거 확인 → (답변 | 근거 없음)
	graph.add_edge("rewrite", "retrieve")
	graph.add_edge("retrieve", "grade")
	graph.add_conditional_edges(
		"grade",
		route_after_grade,
		{"generate": "generate", "no_answer": "no_answer"},
	)

	# 답변 뒤 출처·추천 질문을 동시에 만들고, 둘 다 끝나면 기록
	graph.add_edge("generate", "sources")
	graph.add_edge("generate", "suggest")
	graph.add_edge(["sources", "suggest"], "save_log")

	# 잡담·근거 없음·입력 오류도 기록 후 종료
	graph.add_edge("chat", "save_log")
	graph.add_edge("no_answer", "save_log")
	graph.add_edge("error_reply", "save_log")
	graph.add_edge("save_log", END)
	return graph
