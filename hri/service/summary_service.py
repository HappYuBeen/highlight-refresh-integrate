"""보고서 요약 실행 · summarize_report (김희진)"""
import logging
from langchain_core.callbacks import UsageMetadataCallbackHandler
from hri.dao.usage_dao import insert_usage
from hri.domain.schemas import LlmUsage, ReportSummary
from hri.exception.errors import OutputFormatError
from hri.graph.registry import get_summary_graph
from hri.graph.summary_graph import sort_section_summaries
from hri.llm.llm_client import cost_korean_won, get_usd_krw

logger = logging.getLogger(__name__)

def _cost_krw(model: str, tokens: dict) -> float | None:
    """모델 1개의 원화 비용.
    환율, 단가를 못 구해도(.env MODEL_PRICES가 null 등) 토큰 기록은 남기려고 None으로 둔다."""
    try:
        return cost_korean_won(
            {model: tokens}, get_usd_krw())
    except Exception:
        logger.exception("요약 비용 계산 실패: model=%s", model)
        return None

def _record_usage(usage: UsageMetadataCallbackHandler, report_id: int) -> None:
    """요약에 쓴 AI 사용량을 모델별로 llm_usage에 저장한다 (flow='summary', ref_id=report_id).
    캐시를 읽기만 한 경우(AI 호출 없음)에는 기록할 것이 없어 바로 끝난다.
    비용 계산, DB 오류는 로그만 남기고 삼킨다: 이미 만든 요약을 오류처럼 보이게 하지 않으려고.
    """
    if not usage.usage_metadata:
        return

    try:
        for model, tokens in usage.usage_metadata.items():
            insert_usage(LlmUsage(
                flow="summary",
                ref_id=report_id,
                model=model,
                input_tokens=tokens.get("input_tokens", 0),
                output_tokens=tokens.get("output_tokens", 0),
                cost_krw=_cost_krw(model, tokens)
            ))
    except Exception:
        logger.exception("요약 사용량 기록 실패: report_id=%s", report_id)

def summarize_report(report_id: int) -> ReportSummary:
    """보고서 1건의 3줄 요약과 섹션별 요약을 만들어 돌려준다.
    summary_graph를 실행한다 (load_cache -> load_sections -> fan_out -> summarize_section ∥ -> three_lines -> save).
    reports.summary_json에 캐시가 있으면 LLM을 부르지 않고 캐시를 돌려준다.
    끝나면 쓴 토큰을 insert_usage(flow='summary', ref_id=report_id)로 기록한다.
    예외 OutputFormatError: 섹션이 없어 요약할 수 없거나, LLM 출력이 형식에 맞지 않을 때(view의 on_summarize가 '요약을 만들지 못했습니다.'로 표시)
    """
    graph = get_summary_graph()
    usage = UsageMetadataCallbackHandler()
    try:
        state = graph.invoke(
            {"report_id": report_id},
            config={"callbacks": [usage]})
    finally:
        _record_usage(usage, report_id)

    if state.get("error"):
        raise OutputFormatError(state["error"])

    return ReportSummary(
        report_id=report_id,
        three_lines=state["three_lines"],
        sections=sort_section_summaries(state["section_summaries"])
    )