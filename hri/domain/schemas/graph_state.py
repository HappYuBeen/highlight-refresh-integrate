"""그래프 상태 · IngestState, QAState, SummaryState, AgentState, RouteDecision (엄정현)
 
LangGraph 워크플로우 명세서 '그래프 상태' 행의 필드를 그대로 옮긴다.
 
- 4개 State는 TypedDict: 노드는 바뀐 키만 dict로 반환하고, LangGraph가 키별로 state에 합친다.
- 필드 설명은 Annotated[타입, Field(description=...)]로 단다.
  TypedDict는 `키: 타입 = Field(...)`처럼 기본값을 넣을 수 없어서(LangGraph·Pydantic 모두 무시) Annotated 안에 넣는다.
- 리듀서가 붙은 키는 덮어쓰지 않고 합친다. 리듀서는 Annotated의 맨 마지막에 둔다 (LangGraph가 마지막 값을 리듀서로 읽음).
    - operator.add  : 병렬 노드 결과를 리스트로 이어 붙임 (IngestState.tables·warnings, SummaryState.section_summaries)
    - add_messages  : 대화 메시지를 누적 (QAState.messages, AgentState.messages)
- RouteDecision은 LLM이 with_structured_output()으로 채우는 값이라 Pydantic BaseModel.
 
주의
- LangGraph가 Annotated 리듀서를 읽어야 하므로 `from __future__ import annotations`를 쓰지 않는다.
- TypedDict는 typing이 아니라 typing_extensions에서 가져온다.
  Python 3.11에서 Pydantic(TypeAdapter·BaseModel 필드)이 typing.TypedDict를 거부하기 때문 (3.12부터는 둘 다 됨).
 
사용: from hri.domain.schemas import IngestState
"""
 
import operator
from datetime import datetime
from typing import Annotated, Literal
 
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing_extensions import TypedDict
 
from hri.domain.schemas.qa import ContextDoc
from hri.domain.schemas.report import ChartImage, Chunk, Page, Section, Table
from hri.domain.schemas.summary import SectionSummary
 
__all__: list[str] = ["IngestState", "QAState", "SummaryState", "AgentState", "RouteDecision"]
 
 
# ---------------------------------------------------------------------
# 1. 업로드 흐름 (ingest_graph) · upload_report()가 실행
# ---------------------------------------------------------------------
class IngestState(TypedDict):
    """보고서 업로드 그래프 상태.
 
    validate → [load_text ∥ extract_tables ∥ extract_charts → process_charts] → split → summarize_tables → persist
    tables·warnings는 병렬 노드가 각자 반환한 리스트를 operator.add로 이어 붙인다.
    """
 
    file_path: Annotated[str, Field(description="입력: data/raw/에 저장한 원본 PDF 경로")]
    user_id: Annotated[str, Field(description="입력: 업로드한 사용자 ID (reports.uploaded_by)")]
    meta: Annotated[dict, Field(description="load_text: extract_report_meta() 결과 (title, publisher, published_date)")]
    report_id: Annotated[int | None, Field(description="persist: insert_report()가 돌려준 보고서 PK")]
    pages: Annotated[list[Page], Field(description="load_text: 머리말·꼬리말을 정제하고 목차 앞을 잘라낸 쪽 목록 (1부터)")]
    sections: Annotated[list[Section], Field(description="split: split_sections()로 나눈 섹션 목록 (부모 섹션)")]
    chunks: Annotated[list[Chunk], Field(description="split이 만들고 summarize_tables가 표·차트 청크 text를 채운 최종 청크 목록")]
    charts: Annotated[list[ChartImage], Field(description="extract_charts: PDF에서 잘라낸 차트 이미지 목록")]
    tables: Annotated[
        list[Table],
        Field(description="extract_tables(표)·process_charts(차트) 결과를 합친 원본 표·차트 목록"),
        operator.add,
    ]
    summarized_tables: Annotated[
        list[Table],
        Field(description="summarize_tables: 검색용 1~2문장 summary를 채운 표·차트 목록 (tables 재반환 시 중복되므로 별도 키)"),
    ]
    warnings: Annotated[
        list[str],
        Field(description="스캔본 감지·표 검수 필요·차트 인식 실패 등 처리 중 쌓인 경고"),
        operator.add,
    ]
    counts: Annotated[dict[str, int], Field(description="persist: 저장한 청크 유형별(text/table/chart) 건수")]
    error: Annotated[str | None, Field(description="validate 실패 사유 (ValidationResult.reason). 있으면 END, 정상이면 None")]
 
 
# ---------------------------------------------------------------------
# 2. 질의응답 흐름 (qa_graph) · ask_stream()이 실행
# ---------------------------------------------------------------------
class QAState(TypedDict):
    """질의응답 그래프 상태. 체크포인터(thread_id=session_id)에 저장된다.
 
    messages만 add_messages 리듀서로 턴마다 누적되고,
    나머지 필드는 이전 턴 값이 남으므로 ask_stream()이 매 턴 초기화해서 넣는다.
    """
 
    messages: Annotated[
        list,
        Field(description="대화 기록: 질문(HumanMessage)·답변(AIMessage)이 턴마다 누적"),
        add_messages,
    ]
    question: Annotated[str, Field(description="이번 턴 질문 (mask_pii 적용)")]
    persona: Annotated[str, Field(description="답변 페르소나: 입문자 / 실무자 / 전문가")]
    report_ids: Annotated[list[int], Field(description="검색 대상으로 선택한 보고서 ID 목록")]
    k: Annotated[int, Field(description="검색해 가져올 청크 수 (Top-K)")]
    use_mmr: Annotated[bool, Field(description="MMR 재정렬로 검색 결과 다양성을 높일지 여부")]
    user_id: Annotated[str, Field(description="질문한 사용자 ID (qa_logs.user_id)")]
    route: Annotated[
        Literal["rag", "no_answer", "chat", "error"],
        Field(description="이번 답변의 최종 경로 = 답을 만든 노드: generate→'rag', no_answer→'no_answer', chat→'chat', error_reply→'error' (qa_logs.route와 같은 값)"),
    ]
    rewritten: Annotated[str, Field(description="rewrite: 검색에 맞게 다시 쓴 질문")]
    docs: Annotated[list[Chunk], Field(description="retrieve: hybrid_search()로 찾은 청크 목록")]
    context_docs: Annotated[
        list[ContextDoc],
        Field(description="generate: get_context_docs()로 만든 답변 근거 (텍스트는 부모 섹션 원문, 표·차트는 원본 마크다운)"),
    ]
    has_source: Annotated[bool, Field(description="근거 유무: generate는 True, chat·no_answer·error_reply는 False")]
    answer: Annotated[str, Field(description="최종 답변 (RAG 답변·근거 없음 안내·잡담 응답·입력 오류 안내)")]
    sources: Annotated[
        list[dict],
        Field(description="sources: format_sources() 결과 (보고서명·페이지, 차트는 '그림 n(차트)', 추정값 표시)"),
    ]
    suggestions: Annotated[list[str], Field(description="suggest: 후속 추천 질문 3개")]
    terms: Annotated[list[str], Field(description="suggest: 답변 속 어려운 경제 용어 (최대 3개)")]
    started_at: Annotated[datetime, Field(description="ask_stream 시작 시각 → save_log에서 응답 시간 계산")]
    log_id: Annotated[int | None, Field(description="save_log: save_qa_log()가 돌려준 qa_logs PK")]
    error: Annotated[str | None, Field(description="validate의 InvalidInputError 안내 문구 → error_reply로 분기")]
 
 
# ---------------------------------------------------------------------
# 3. 요약 흐름 (summary_graph) · summarize_report()가 실행
# ---------------------------------------------------------------------
class SummaryState(TypedDict):
    """보고서 요약 그래프 상태.
 
    fan_out_sections가 섹션마다 Send('summarize_section', {'section': s})로 나눠 보내고(map),
    각 노드가 반환한 section_summaries를 operator.add로 모은다(reduce).
    """
 
    report_id: Annotated[int, Field(description="입력: 요약할 보고서 ID (reports.report_id)")]
    sections: Annotated[list[Section], Field(description="load_sections: select_sections()로 조회한 섹션 목록 (페이지 순)")]
    section_summaries: Annotated[
        list[SectionSummary],
        Field(description="summarize_section 병렬 노드들의 섹션별 요약을 합친 목록"),
        operator.add,
    ]
    three_lines: Annotated[list[str], Field(description="three_lines: 보고서 전체 3줄 요약")]
    error: Annotated[str | None, Field(description="섹션 0개 등 요약 불가 사유 → END, 정상이면 None")]
 
 
# ---------------------------------------------------------------------
# 4. Tool Calling 흐름 (agent_graph) · run_agent_with_tools()가 실행
# ---------------------------------------------------------------------
class AgentState(TypedDict):
    """에이전트 그래프 상태. agent ↔ tools 루프에서 AIMessage·ToolMessage가 messages에 쌓인다."""
 
    messages: Annotated[
        list,
        Field(description="system·사용자 요청·도구 호출(AIMessage.tool_calls)·도구 결과(ToolMessage) 누적"),
        add_messages,
    ]
    report_ids: Annotated[list[int], Field(description="화면에서 선택한 보고서 ID 목록 (도구 호출 인자로 사용)")]
 
 
# ---------------------------------------------------------------------
# 5. 질문 분류 (route_question의 LLM 분류 결과)
# ---------------------------------------------------------------------
class RouteDecision(BaseModel):
    """route_question()이 정규식·키워드로 판단하지 못한 질문을
    get_chat_model(temperature=0).with_structured_output(RouteDecision)으로 분류한 결과."""
 
    route: Literal["rewrite", "chat"] = Field(
        description="보고서 내용을 찾아 답해야 하는 질문이면 'rewrite', 인사·감사·잡담이면 'chat'",
    )

