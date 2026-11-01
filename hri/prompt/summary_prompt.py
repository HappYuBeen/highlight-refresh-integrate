"""요약·검증·병합 · summarize_sections, summarize_three_lines, verify_and_expand, merge_reports (김희진)"""
import logging
from datetime import datetime
from hri.domain.schemas import Section, SectionSummary, ThreeLineSummary, Indicator, MergedReport, VerifyResult
from hri.exception.errors import OutputFormatError
from hri.llm.llm_client import get_chat_model

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2 # 형식이 어긋났을 때 시도하는 횟수 (첫 1번 + 재시도 1번)
MAX_SECTION_CHARS = 8000 # 섹션 원문을 LLM에 보내는 최대 글자 수
SECTION_MAX_TOKENS = 3000
THREE_LINES_MAX_TOKENS = 3000
LINE_MAX_CHARS = 60 # ThreeLineSummary.lines 설명의 "각 줄 60자 이내"와 같은 값
MIN_MERGE_REPORTS = 2 # 병합하려면 필요한 보고서 수
MAX_MERGE_CHARS = 4000 # 보고서 1건의 섹션 요약을 LLM에 보내는 최대 글자 수
MERGE_MAX_TOKENS = 6000
VERIFY_MAX_TOKENS = 4000
MAX_REPORT_ROWS = 30 # LLM에 보내는 보고서 수치 줄 수
MAX_ECOS_ROWS = 20 # LLM에 보내는 ECOS 값 줄 수

NO_INDICATORS_NOTE = "보고서에서 비교할 수치를 찾지 못했습니다."
NO_ECOS_QUERY_NOTE = "보고서 수치와 비교할 ECOS 통계를 찾지 못했습니다."
NO_ECOS_VALUE_NOTE = "{name}: ECOS에서 조건에 맞는 통계 값을 찾지 못했습니다."
DEFAULT_VERIFY_REQUEST = "보고서 수치를 ECOS 최신 통계와 비교해 주세요."

SECTION_SYSTEM_PROMPT = (
    "당신은 경제 보고서를 요약하는 애널리스트입니다.\n"
    "주어진 섹션 1개의 핵심 내용을 2~3문장으로 요약하세요.\n"
    "- 수치와 단위는 원문 그대로 씁니다.\n"
    "- 원문에 없는 내용은 쓰지 않습니다.\n"
    "- title에는 섹션 제목을 그대로 씁니다."
)
THREE_LINES_SYSTEM_PROMPT = (
    "당신은 경제 보고서를 요약하는 애널리스트입니다.\n"
    "아래는 한 보고서의 섹션별 요약입니다. 이를 바탕으로 보고서 전체를 3줄로 요약하세요.\n"
    "- 1줄: 핵심 주장\n"
    "- 2줄: 그 주장을 뒷받침하는 근거 수치\n"
    "- 3줄: 시사점\n"
    f"- 각 줄은 {LINE_MAX_CHARS}자 이내의 한 문장으로 씁니다.\n"
    "- 수치와 단위는 요약에 있는 그대로 쓰고, 요약에 없는 내용은 만들지 않습니다.\n"
    "- 줄 앞에 번호나 기호를 붙이지 않습니다."
)
MERGE_SYSTEM_PROMPT = (
    "당신은 여러 경제 보고서를 비교해 정리하는 애널리스트입니다.\n" \
    "아래는 보고서별 섹션 요약입닏. 보고서는 '### 보고서 제목'으로 구분되어 있습니다.\n" \
    "- common: 모든 보고서가 공통으로 말하는 내용. 항목마다 근거 보고서 제목을 괄호로 표시합니다.\n" \
    "- differences: 보고서 간 의견이나 수치가 다른 부분. 예) '2026 성장률 전망: A 2.1% B 1.8%'\n" \
    "- conclusion: 공통점과 차이를 종합한 결론을 3문장 이내로 씁니다.\n" \
    "- 수치와 단위는 요약에 있는 그대로 쓰고, 요약엥 없는 내용은 만들지 않습니다."
)
VERIFY_SYSTEM_PROMPT = (
    "당신은 경제 보고서의 수치를 한국은행 ECOS 통계와 비겨해 검증하는 애널리스트입니다.\n" \
    "사용자 메시지의 [보고서 수치]와 [ECOS 값]만 근거로 씁니다. 목록에 없는 수치는 만들지 않습니다.\n" \
    "- 지표 이름·기준 시점·단위가 같은 값끼리만 비교합니다\n" \
    "- 값이 같으면 [일치], 반올림 수준으로 가까우면 [근사], 그 밖에는 [차이]로 표시합니다.\n" \
    "- matches: [일치]·[근거] 항목, differences: [차이] 항목. 형식은 " \
    "'지표명(기준 시점): 보고서 값 / ECOS 값 [상태] (출처: 한국은행 ECOS 통계표코드)'\n" \
    "- unchecked: 비교하지 못한 항목과 이유 (기준 시점이 다름, 단위가 다름, 미래 전망, ECOS 값 없음 등)\n" \
    "- additions: 보고서의 근거가 부족한 부분을 ECOS 값으로 보완한 문단. 문장마다 (출처: 한국은행 ECOS 통계표코드)를 붙입니다.\n" \
    "- '(추정)'으로 표시된 보고서 수치는 차트에서 읽은 추정값이므로 결과에도 추정값이라고 밝힙니다."
)

def _invoke_with_retry(model, messages, is_valid):
    """구조화 출력 호출을 MAX_ATTEMPTS번까지 시도해 검증을 통과한 결과를 돌려준다.
    모두 실패하면 None."""
    for _ in range(MAX_ATTEMPTS):
        try:
            result = model.invoke(messages)
        except Exception:
            logger.exception("구조화 출력 호출 실패 (재시도 가능)")
            continue
        if result is not None and is_valid(result):
            return result
    return None

def summarize_sections(sections: list[Section]) -> list[SectionSummary]:
    """
    섹션마다 LLM으로 2~3문장 요약(SectionSummary)을 만들어 입력 순서대로 돌려준다.
    summary_graph의 summarize_section 노드가 섹션 1개씩 [section]으로 호출한다. 빈 목록이면 [].
    title은 원문 제목으로 덮어쓰고, section_id(=section_no)와 start_page, end_page는 Section에서 채운다.
    원문이 MAX_SECTION_CHARS를 넘으면 앞부분만 보낸다.
    형식이 어긋나면 MAX_ATTEMPTS번까지 시도하고, 계속 실패하면 OutputFormatError를 낸다.
    """
    if not sections:
        return []

    model = get_chat_model(temperature=0, max_tokens=SECTION_MAX_TOKENS).with_structured_output(SectionSummary)
    results: list[SectionSummary] = []

    for section in sections:
        messages = [
            ("system", SECTION_SYSTEM_PROMPT),
            ("human", f"섹션 제목: {section.title}\n\n본문:\n{section.text[:MAX_SECTION_CHARS]}"),
        ]
        summary = _invoke_with_retry(model, messages, lambda x: bool(x.summary.strip()))

        if summary is None:
            raise OutputFormatError(f"섹션 요약 형식이 올바르지 않습니다. (section_no={section.section_no})")
        results.append(summary.model_copy(update={
            "title": section.title,
            "summary": summary.summary.strip(),
            "section_id": section.section_no,
            "start_page": section.start_page,
            "end_page": section.end_page,
        }))
    
    return results

def summarize_three_lines(section_summaries: list[SectionSummary]) -> ThreeLineSummary:
    """
    섹션별 요약을 모아 보고서 전체를 3줄(핵심 주장 / 근거 수치 / 시사점)로 요약한다.
    summary_graph의 three_lines 노드가 사용하며, 호출부는 결과의 lines를 쓴다.
    section_summaries는 start_page 순으로 정렬해서 넘긴다. 빈 목록이면 ValueError.
    형식이 어긋나면 MAX_ATTEMPTS번까지 시도하고, 계속 실패하면 OutputFormatError를 낸다.
    """
    if not section_summaries:
        raise ValueError("섹션 요약이 없어 3줄 요약을 만들 수 없습니다.")
    body = "\n\n".join(f"[{s.title}]\n{s.summary}" for s in section_summaries)
    messages = [
        ("system", THREE_LINES_SYSTEM_PROMPT),
        ("human", f"섹션별 요약:\n\n{body}"),
    ]
    model = get_chat_model(temperature=0, max_tokens=THREE_LINES_MAX_TOKENS).with_structured_output(ThreeLineSummary)
    result = _invoke_with_retry(model, messages, lambda x: all(line.strip() for line in x.lines))

    if result is None:
        raise OutputFormatError("3줄 요약 형식이 올바르지 않습니다.")
    return result.model_copy(update={"lines": [line.strip() for line in result.lines]})

def _report_titles(report_ids: list[int]) -> dict[int, str]:
    """report_id -> 제목. DB에서 못 찾으면 '보고서 번호'를 제목으로 쓴다."""
    from hri.dao.report_dao import select_reports

    try:
        found = {r.report_id: r.title for r in select_reports("")}
    except Exception:
        logger.exception("보고서 제목 조회 실패")
        found = {}

    return {report_id: found.get(report_id, f"보고서 {report_id}") for report_id in report_ids}

def _merge_body(summary) -> str:
    """보고서 1건의 섹션 요약을 '[섹션 제목] 요약' 줄로 이어 붙인다.
    전체가 MAX_MERGE_CHARS자를 넘으면 섹션마다 같은 글자 수로 줄여서, 뒤쪽 섹션이 통째로 빠지지 않게 한다.
    """
    lines = [f"[{s.title}] {s.summary}" for s in summary.sections]
    if not lines:
        return ""
    if sum(len(line) + 1 for line in lines) <= MAX_MERGE_CHARS:
        return "\n".join(lines)
    per_line = max(MAX_MERGE_CHARS // len(lines) - 1, 50)

    return "\n".join(line[:per_line] for line in lines)[:MAX_MERGE_CHARS]

def merge_reports(report_ids: list[int]) -> MergedReport:
    """여러 보고서를 공통 의견·차이점·종합 결론으로 병합한다.
    
    순서
    1. 같은 번호는 한 번만 쓰고, 보고서가 2개 미만이면 서비스·LLM을 부르지 않고 ValueError를 낸다
    2. 보고서 마다 summarize_report()로 요약을 가져온다 (캐시가 있으면 LLM 호출 없음)
    3. '### 보고서 제목' 구분자로 이어 붙여 한 번에 LLM(with_structured_output(MergedReport))에 보낸다
        형식이 어긋나면(결론이 비었거나 공통·차이가 모두 빔) MAX_ATTEMPTS번까지 시도하고, 계속 실패하면 OutputFormatError를 낸다.
        요약 단계의 OutputFormatError는 그대로 올라간다 (이 경우 병합 LLM은 부르지 않는다).
    """
    ids = list(dict.fromkeys(report_ids or []))

    if len(ids) < MIN_MERGE_REPORTS:
        raise ValueError("병합하려면 보고서가 2개 이상 필요합니다.")
    from hri.service.summary_service import summarize_report

    titles = _report_titles(ids)
    counts = {}
    for title in titles.values():
        counts[title] = counts.get(title, 0) + 1
    labels = {report_id: title if counts[title] == 1 else f"{title} (보고서 {report_id})" for report_id, title in titles.items()}
    parts = [f"### {labels[report_id]}\n{_merge_body(summarize_report(report_id))}" for report_id in ids]
    messages = [
        ("system", MERGE_SYSTEM_PROMPT),
        ("human", "\n\n".join(parts)),
    ]
    model = get_chat_model(
        temperature=0,
        max_tokens=MERGE_MAX_TOKENS
    ).with_structured_output(MergedReport)
    result = _invoke_with_retry(model, messages, lambda x: bool(x.conclusion.strip()) and bool(x.common or x.differences))

    if result is None:
        raise OutputFormatError("병합 결과 형식이 올바르지 않습니다.")

    return result.model_copy(update={"conclusion": result.conclusion.strip()})

# 검증 보완
def _value_line(indicator: Indicator) -> str:
    """지펴 1개를 '- 이름 (시점): 값 단위'줄로 만든다.
    차트에서 읽어 확인이 필요한 값(needs_review)은 '(추정)'을 붙인다.
    """
    line = f"- {indicator.name} ({indicator.period}): {indicator.value} {indicator.unit}"

    return line + " (추정)" if indicator.needs_review else line

def verify_and_expand(report_id: int, request: str) -> VerifyResult:
    """보고서의 수치를 ECOS 최신 통계와 비교해 일치·차이를 표시하고, 근거가 부족한 부분을 보완한다.
    
    순서
    1. extract_indicators(report_id)로 보고서의 수치를 가져온다
    2. find_usable_data(indicators)로 ECOS에서 확인할 지표 1개와 조회 조건(EcosQuery)을 고른다
    3. fetch_indicators(query, 지표 이름)로 ECOS 값을 가져온다
    4. 사용자 요청·보고서 수치와 전망(고른 지표만)·ECOS 값을 LLM(with_structured_output(VerifyResult))에 보낸다
        LLM은 일치·근사는 matches, 차이는 differences, 비교하지 못한 것은 unchecked, ECOS 값으로 보완한 문단은 additions에 담는다
    5. checked_at은 코드가 지금 시각으로 채운다

    지표가 없거나 비교할 통계·값이 없으면 LLM을 부르지 않고 VerifyResult(unchecked=[이유])를 돌려준다.
    형식이 어긋나면(모든 항목이 빔) MAX_ATTEMPTS번까지 시도하고, 계속 실패하면 OutputFormatError를 낸다.
    ECOS 호출 실패(ExternalAliError)는 그대로 올라간다 (도구·화면이 안내 문구로 바꾼다).
    """
    from hri.api.ecos_client import fetch_indicators
    from hri.api.ecos_finder import find_usable_data
    from hri.service.indicator_service import extract_indicators

    indicators = extract_indicators(report_id)

    if not indicators:
        return VerifyResult(unchecked=[NO_INDICATORS_NOTE])
    query = find_usable_data(indicators)

    if query is None:
        return VerifyResult(unchecked=[NO_ECOS_QUERY_NOTE])
    compared_name = query.indicator_name.strip()
    ecos_values = fetch_indicators(query, query.indicator_name)
    compared = [i for i in indicators if i.name.strip() == compared_name]
    
    if not ecos_values or not compared:
        return VerifyResult(unchecked=[NO_ECOS_VALUE_NOTE.format(name=compared_name)])
    report_lines = "\n".join(_value_line(i) for i in compared[:MAX_REPORT_ROWS])
    ecos_lines = "\n".join(_value_line(i) for i in ecos_values[:MAX_ECOS_ROWS])
    prompt = (
        f"[사용자 요청]\n{(request or '').strip() or DEFAULT_VERIFY_REQUEST}\n\n"
        f"[보고서 수치]\n{report_lines}\n\n"
        f"[ECOS 값]\n[ECOS {query.stat_code}]\n{ecos_lines}"
    )
    messages =[
        ("system", VERIFY_SYSTEM_PROMPT),
        ("human", prompt)
    ]
    model = get_chat_model(
        temperature=0,
        max_tokens=VERIFY_MAX_TOKENS
    ).with_structured_output(VerifyResult)
    result = _invoke_with_retry(model, messages, lambda x: bool(x.matches or x.differences or x.unchecked or x.additions))

    if result is None:
        raise OutputFormatError("검증 결과 형식이 올바르지 않습니다.")

    # checked_at은 LLM이 아니라 코드가 지금 시각으로 채운다 (LLM이 꾸민 날짜를 쓰지 않으려고)
    return result.model_copy(update={"checked_at": datetime.now()})