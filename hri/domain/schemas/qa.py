"""질의응답 스키마 · AnswerResult, SuggestOutput, SearchHit, ContextDoc (서동안)

질의응답 설계서(docs/qa_design.md) 8장 '데이터 형식'과 같은 필드를 쓴다.
Chunk는 보고서 스키마(report.py)에 있고, 여기서는 가져와서 SearchHit에만 쓴다.

사용: from hri.domain.schemas import AnswerResult
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from hri.domain.schemas.report import Chunk

# __init__.py가 `import *`로 다시 내보내므로, 내보낼 이름만 적는다
# (없으면 Chunk·BaseModel·Field·annotations까지 hri.domain.schemas로 새어 나간다)
__all__: list[str] = ["AnswerResult", "SuggestOutput", "SearchHit", "ContextDoc"]


class AnswerResult(BaseModel):
	"""ask_stream()이 답변 조각을 모두 보낸 뒤 마지막에 넘기는 질문 1건의 결과.

	respond_stream()이 받아 출처·추천 질문 영역과 state['last_log_id'](👍/👎 피드백용)를 갱신한다.
	"""

	question: str = Field(description="mask_pii를 적용한 이번 질문")
	answer: str = Field(description="최종 답변 (RAG 답변·'자료에 없습니다'·잡담 응답·입력 오류 안내)")
	sources: list[dict] = Field(default_factory=list, description="format_sources() 결과 (보고서명·페이지)")
	suggestions: list[str] = Field(default_factory=list, description="후속 추천 질문 (rag일 때 3개, 그 외 빈 리스트)")
	has_source: bool = Field(default=False, description="근거 유무: route='rag'일 때만 True")
	route: Literal["rag", "no_answer", "chat", "error"] = Field(description="최종 경로 (qa_logs.route와 같은 값)")
	tokens: int = Field(default=0, ge=0, description="이번 질문에서 쓴 전체 토큰 수")
	cost_krw: float = Field(default=0.0, ge=0, description="원화 예상 비용")
	log_id: int | None = Field(default=None, description="qa_logs PK (피드백 저장에 사용)")


class SuggestOutput(BaseModel):
	"""질의응답 그래프의 suggest 노드가 with_structured_output()으로 받는 후속 질문."""

	questions: list[str] = Field(
		min_length=3,
		max_length=3,
		description="질문과 답변에 이어서 물어볼 만한 후속 질문 정확히 3개",
	)


class SearchHit(BaseModel):
	"""vector_search()의 의미 검색 결과 1건."""

	chunk: Chunk
	score: float = Field(description="관련도 = 1 - 코사인 거리 (safe_search가 THRESHOLD와 비교)")
	embedding: list[float] = Field(description="청크 임베딩. mmr_select()가 유사도 계산에 사용")


class ContextDoc(BaseModel):
	"""get_context_docs()가 만들고 format_docs()·format_sources()에 넘기는 근거 1건.

	텍스트: 부모 섹션 원문 (page_start~page_end = 섹션 범위)
	표·차트: report_tables 원본 마크다운 (page_start = page_end = 표가 있는 쪽)
	"""

	report_id: int
	report_title: str
	kind: Literal["text", "table", "chart"]
	section_title: str | None = Field(default=None, description="kind='text'일 때 섹션명")
	caption: str | None = Field(default=None, description="kind='table'·'chart'일 때 캡션")
	page_start: int = Field(ge=1, description="시작 쪽, 1부터")
	page_end: int = Field(ge=1, description="끝 쪽, 1부터")
	text: str = Field(description="[자료]에 들어갈 본문 (섹션 원문 또는 표 마크다운)")
	needs_review: bool = Field(default=False, description="차트 OCR 추정값이면 True ('(추정값)' 표시)")

	@model_validator(mode="after")
	def _check_pages(self) -> ContextDoc:
		"""끝 쪽이 시작 쪽보다 앞이면 출처 표시('p.5-2')가 틀어지므로 막는다."""
		if self.page_end < self.page_start:
			raise ValueError(f"page_end({self.page_end})가 page_start({self.page_start})보다 작습니다")
		return self