"""LLM 사용량 기록 · insert_usage (임세준)

LLM을 쓴 뒤 모델 이름과 입력·출력 토큰 수를 llm_usage 테이블에 저장하는 DAO.
업로드(ingest)·질의응답(qa)·요약(summary)·요청 실행(agent) 서비스가
그래프 실행이 끝나면 insert_usage()를 부른다.
원화 비용(cost_krw)은 DB에 저장하지 않는다 (질의응답 비용은 qa_logs에 저장).
"""
from hri.dao.db import get_connection
from hri.domain.schemas import LlmUsage


def insert_usage(usage: LlmUsage) -> int:
	"""
	LLM 사용량 한 줄 저장하기

	LlmUsage에 담긴 값을 llm_usage 테이블에 INSERT 한다.
	- flow: 어디서 썼는지 (qa / ingest / summary / agent)
	- user_id: 사용한 사람 (없으면 NULL로 저장)
	- ref_id: 연결된 번호 (질문 기록 log_id, 보고서 report_id 등, 없으면 NULL)
	- model, input_tokens, output_tokens: 모델 이름, 입력·출력 토큰 수
	created_at(저장한 시간)은 DB가 알아서 넣는다.
	usage.cost_krw는 참고용이라 값이 있어도 저장하지 않는다.

	순서
	1. sql: 저장할 SQL을 만든다 (값 자리는 %s로 비워 둠)
	2. values: %s 자리에 순서대로 들어갈 값을 튜플로 만든다
	3. get_connection()으로 DB에 연결해서 실행하고 commit으로 저장한다
	   (with 문이 끝나면 연결이 자동으로 닫힘)
	4. cursor.lastrowid로 방금 저장한 줄의 번호(usage_id)를 받아서 돌려준다

	입력: usage (LlmUsage)
	출력: int (방금 저장한 줄의 번호)
	"""
	sql = """
		INSERT INTO llm_usage (flow, user_id, ref_id, model, input_tokens, output_tokens)
		VALUES (%s, %s, %s, %s, %s, %s)
	"""

	values = (
		usage.flow,
		usage.user_id,
		usage.ref_id,
		usage.model,
		usage.input_tokens,
		usage.output_tokens
	)

	with get_connection() as conn:
		cursor = conn.cursor()
		cursor.execute(sql, values)
		conn.commit()
		usage_id = cursor.lastrowid

	return usage_id