"""회원·기록 스키마 · User, QALog, LlmUsage (임세준)

회원 정보, 질문 기록, LLM 사용량 기록을 담는 Pydantic 모델 모음.
- User: 로그인한 회원 정보 (user_service, user_view에서 사용)
- QALog: 질문 한 번의 기록 (질문 기록 저장·조회, 내 기록 화면, 관리자 대시보드에서 사용)
- LlmUsage: LLM을 한 번 쓸 때의 토큰·비용 기록 (업로드·질의응답·요약·요청 실행 뒤에 저장)
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

# 다른 파일에서 from hri.domain.schemas import User 로 쓸 수 있게 이름 등록
__all__ = ["User", "QALog", "LlmUsage"]


class User(BaseModel):
	"""
	회원 정보

	users 테이블의 회원 한 명을 담는다.
	login()이 로그인에 성공하면 이 모델을 돌려주고,
	on_login()이 여기서 user_id·name·role·created_at을 꺼내 gr.State에 저장한다.
	register()가 회원가입할 때도 이 모델을 만들어 insert_user()에 넘긴다.

	- role이 "admin"이면 관리자 탭이 보인다. 회원가입하면 항상 "user"
	- 비밀번호는 담지 않는다 (로그인할 때만 select_user_auth()로 비교)

	사용하는 곳: register(), login(), insert_user(), on_login()
	"""

	user_id: str = Field(description="아이디")
	name: str = Field(description="이름")
	role: Literal["user", "admin"] = Field(description="역할 (user: 일반 사용자, admin: 관리자)")
	created_at: datetime = Field(description="가입한 시간")


class QALog(BaseModel):
	"""
	질문 기록 한 줄 (qa_logs 테이블)

	사용자가 질문을 한 번 하면 질문·답변과 그때의 정보를 이 모델에 담아 저장한다.
	저장하기 전에는 log_id·created_at이 없고(None), DB에 저장하면 번호와 시간이 생긴다.
	feedback은 답변 아래 👍/👎 버튼을 누르면 나중에 채워진다.

	- route로 답변 종류를 나눈다 (rag / no_answer / chat / error)
	- 대시보드에서 질문 TOP 10, 자료 유무 비율, 평균 응답 시간, 비용, 최근 에러를 이 기록으로 계산한다

	사용하는 곳: save_qa_log(), insert_qa_log(), select_logs(), get_history(), 대시보드
	"""

	log_id: Optional[int] = Field(default=None, description="기록 번호 (저장하기 전에는 None)")
	user_id: str = Field(description="질문한 사람 아이디")
	session_id: str = Field(description="대화 묶음 번호")
	question: str = Field(description="질문")
	answer: str = Field(description="답변")
	persona: Literal["입문자", "실무자", "전문가"] = Field(description="페르소나 (입문자, 실무자, 전문가)")
	route: Literal["rag", "no_answer", "chat", "error"] = Field(
		description="답변 종류 (rag: 자료로 답함, no_answer: 자료 없음, chat: 잡담, error: 입력 오류)"
	)
	has_source: bool = Field(description="근거 자료가 있었는지")
	response_ms: int = Field(description="응답 시간 (ms)", ge=0)
	tokens: int = Field(description="사용한 토큰 수", ge=0)
	cost_krw: float = Field(description="원화 비용", ge=0)
	feedback: Optional[int] = Field(default=None, description="피드백 (1: 👍, -1: 👎, 누르기 전에는 None)")
	error: Optional[str] = Field(default=None, description="오류 내용 (없으면 None)")
	created_at: Optional[datetime] = Field(default=None, description="기록한 시간 (저장하기 전에는 None)")


class LlmUsage(BaseModel):
	"""
	LLM 사용량 기록 한 줄 (llm_usage 테이블)

	업로드·질의응답·요약·요청 실행에서 LLM을 쓴 뒤
	어떤 모델로 토큰을 얼마나 썼는지 이 모델에 담아 저장한다.
	flow로 어디서 썼는지 나누고, ref_id로 질문 기록(log_id)이나 보고서(report_id)와 연결한다.

	- 업로드처럼 질문 기록이 없는 경우도 있어서 user_id·ref_id는 없어도 된다(None)
	- 비용(cost_krw)은 참고용으로만 담는 값이다 (DB llm_usage에는 저장하지 않음)
	  넣지 않아도 되고(None), 넣어도 insert_usage()가 저장하지 않는다
	- 대시보드의 flow별 토큰 사용량 합계를 이 기록으로 계산한다

	사용하는 곳: insert_usage(), upload_report(), save_log_node(), summarize_report(), run_agent_with_tools()
	"""

	flow: Literal["qa", "ingest", "summary", "agent"] = Field(
		description="어디서 썼는지 (qa: 질의응답, ingest: 업로드, summary: 요약, agent: 요청 실행)"
	)
	user_id: Optional[str] = Field(default=None, description="사용한 사람 아이디 (없으면 None)")
	ref_id: Optional[int] = Field(default=None, description="연결된 번호 (질문 기록 log_id, 보고서 report_id 등)")
	model: str = Field(description="사용한 모델 이름")
	input_tokens: int = Field(description="입력 토큰 수", ge=0)
	output_tokens: int = Field(description="출력 토큰 수", ge=0)
	cost_krw: Optional[float] = Field(default=None, description="원화 비용 (참고용, DB에는 저장하지 않음)", ge=0)
	created_at: Optional[datetime] = Field(default=None, description="기록한 시간 (저장하기 전에는 None)")