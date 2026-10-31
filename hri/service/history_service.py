"""질문 기록·세션 유지 · save_qa_log, get_history, restore_session (임세준)

질문 기록을 저장하고, 내 기록 화면과 대화 이어가기에 필요한 기록을 불러오는 서비스.
- save_qa_log(): 질의응답이 끝나면 save_log_node()가 불러 기록을 남긴다 (개인정보를 가린 뒤 저장)
- get_history(): 로그인 사용자의 질문 기록을 최신순으로 불러온다 (내 기록 화면)
- restore_session(): 고른 대화를 다시 불러와 채팅 화면에 채우고, 질의응답 그래프의 대화 기억도 되살린다
"""
from langchain_core.messages import AIMessage, HumanMessage

from hri.dao.qa_log_dao import insert_qa_log, select_logs
from hri.domain.schemas import QALog


def save_qa_log(log: QALog) -> int:
	"""
	질문 기록 저장하기 (개인정보 가린 뒤)

	순서
	1. 가린 질문·답변으로 새 QALog를 만든다
	   (받은 log는 그대로 두고 새로 만들어서, 부른 쪽의 값이 바뀌지 않게 함)
	2. insert_qa_log()로 qa_logs에 저장하고 log_id를 돌려준다

	- 저장하는 값: 질문·답변·페르소나·route('rag'|'no_answer'|'chat'|'error')·근거 유무·
	  응답 시간·토큰 수·원화 비용·오류 내용 (+ user_id·session_id)
	- 돌려준 log_id는 save_log_node()가 insert_usage(flow='qa', ref_id=log_id)에 쓴다
	- qa_service가 그래프(save_log_node)를 거쳐 이 파일을 다시 불러오면
	  서로가 서로를 부르는 순환 import가 될 수 있어서, 맨 위가 아니라 함수 안에서 불러온다

	입력: log (QALog, log_id·created_at은 None)
	출력: int (저장된 기록 번호 log_id)
	"""
	from hri.service.qa_service import mask_pii

	# 1) 질문·답변의 개인정보 가리기
	masked_question = mask_pii(log.question)
	masked_answer = mask_pii(log.answer)

	# 2) 가린 값으로 새 QALog 만들기 (나머지 값은 그대로)
	masked_log = QALog(
		user_id=log.user_id,
		session_id=log.session_id,
		question=masked_question,
		answer=masked_answer,
		persona=log.persona,
		route=log.route,
		has_source=log.has_source,
		response_ms=log.response_ms,
		tokens=log.tokens,
		cost_krw=log.cost_krw,
		error=log.error
	)

	# 3) 저장하고 log_id 돌려주기
	log_id = insert_qa_log(masked_log)

	return log_id


def get_history(user_id: str) -> list[QALog]:
	"""
	로그인 사용자의 질문 기록 불러오기 (최신순)

	select_logs(user_id, session_id=None)으로 그 사람의 모든 대화 기록을 최신순으로 가져온다.
	내 기록 화면(3.6.8)이 이 결과를 session_id별로 묶어서 대화 목록을 만든다.

	입력: user_id (로그인한 사람 아이디)
	출력: list[QALog] (최신순, 기록이 없으면 빈 리스트 [])
	"""
	logs = select_logs(user_id, session_id=None)

	return logs


def restore_session(session_id: str, user_id: str) -> list[dict]:
	"""
	고른 대화 다시 불러오기 (대화 이어가기)

	순서
	1. select_logs()로 그 대화(session_id)의 기록을 가져온 뒤,
	   최신순으로 오기 때문에 뒤집어서 created_at 순(오래된 것부터)으로 만든다
	2. 기록 한 줄마다
	   - 질문은 HumanMessage, 답변은 AIMessage로 바꿔 messages에 담는다 (그래프 기억용)
	   - {'role': 'user'/'assistant', 'content': ...}로 바꿔 chat_history에 담는다 (gr.Chatbot 표시용)
	   - 화면 표시용 질문은 * _ 앞에 \를 붙여, 가린 글자가 마크다운 기울임으로 바뀌지 않게 한다
	3. get_qa_graph().get_state()로 그 대화(thread)에 messages가 이미 있는지 확인한다
	   - 이미 있으면 건너뛴다 (같은 대화가 두 번 쌓이지 않게)
	   - 없으면 qa_graph.update_state({'configurable': {'thread_id': session_id}}, {'messages': [...]})로
	     체크포인터에 다시 채운다 (서버가 재시작돼서 InMemorySaver가 비어도 이어서 질문할 수 있음)
	4. gr.Chatbot에 넣을 chat_history를 돌려준다

	- 명세서 입력은 session_id 하나지만, select_logs()가 user_id(필수)로 찾기 때문에
	  user_id도 함께 받는다 (다른 사람의 대화를 불러오지 못하게 막는 역할도 함)
	- 그래프의 save_log_node가 이 파일을 불러오므로 순환 import를 피하려고 함수 안에서 불러온다

	입력: session_id (불러올 대화 번호), user_id (로그인한 사람 아이디)
	출력: list[dict] (gr.Chatbot용 [{'role': 'user', 'content': 질문}, {'role': 'assistant', 'content': 답변}, ...])
	"""
	from hri.graph.registry import get_qa_graph

	# 1) 그 대화의 기록 가져와서 오래된 것부터 순서로 뒤집기
	logs = select_logs(user_id, session_id)
	logs.reverse()

	# 2) 그래프 기억용 messages와 화면 표시용 chat_history 만들기
	messages = []
	chat_history = []
	for log in logs:
		messages.append(HumanMessage(content=log.question))
		messages.append(AIMessage(content=log.answer))

		# 가린 글자(홍*동, use***)의 * _가 마크다운 기울임으로 바뀌지 않게 앞에 \를 붙임 (화면 표시용만)
		shown_question = log.question.replace("*", "\\*").replace("_", "\\_")
		chat_history.append({"role": "user", "content": shown_question})
		chat_history.append({"role": "assistant", "content": log.answer})

	# 3) 그래프의 대화 기억(체크포인터)에 없을 때만 다시 채우기
	qa_graph = get_qa_graph()
	config = {"configurable": {"thread_id": session_id}}

	snapshot = qa_graph.get_state(config)
	saved_messages = snapshot.values.get("messages", [])

	if len(saved_messages) == 0 and len(messages) > 0:
		qa_graph.update_state(config, {"messages": messages})

	# 4) 화면 표시용 대화 돌려주기
	return chat_history