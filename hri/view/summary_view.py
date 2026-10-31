"""요약·분석 탭 · build_summary_tab, on_summarize (김희진)"""
import logging
import gradio as gr
from hri.util.logger import safe_call
from hri.exception.errors import OutputFormatError
from hri.service.report_service import search_reports

logger = logging.getLogger(__name__)

# app.py의 요약, 분석 탭 id (report_view.SUUMARY_TAB_ID와 같아야 한다)
MAX_MERGE_REPORTS = 5
SUMMARY_TAB_ID = "summary"
NEED_REPORT_MESSAGE = "보고서를 선택하세요."
NEED_MERGE_MESSAGE = "병합하려면 보고서를 2개 이상 선택하세요."
SUMMARY_FAIL_MESSAGE = "요약을 만들지 못했습니다."
MERGE_FAIL_MESSAGE = "병합 결과를 만들지 못했습니다."
MERGE_ERROR_MESSAGE = "병합 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
SUMMARY_ERROR_MESSAGE = "요약 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
MERGE_TOO_MANY_MESSAGE = "한번에 병합할 수 있는 보고서는 {max}개 까지 입니다."
MERGE_DUPLICATE_NOTE = "⚠️ 제목이 같은 보고서가 있습니다. 같은 문서를 중복 업로드했다면 같은 내용이 '공통 의견'으로 나올 수 있습니다."
VIEW_THREE_LINES = "3줄 요약"
VIEW_SECTIONS = "섹션별 요약"
VIEW_CHOICES = [VIEW_THREE_LINES, VIEW_SECTIONS]

# app.py, 다른 담당자가 컴포넌트를 꺼내 쓸때 쓰는 모음 (login_components 등과 같은 방식)
summary_components = {}

def _summarize_report(report_id: int):
    """summarize_report를 호출하는 시점에 불러와 실행한다.
    summary_service는 그래프(.env 설정 필요)를 불러오는 모듈이라, 화면 파일을 불러올 때 가 아니라
    실제로 요약할 때 가져온다 (화면 테스트가 .env 없이도 돌아가고, 테스트에서 이 함수만 바꿔 끼울 수 있다.)
    """
    from hri.service.summary_service import summarize_report
    
    return summarize_report(report_id)

def _merge_reports(report_ids: list[int]):
    """merge_reports를 호출하는 시점에 불러와 실행한다
    (이유는 _summarize_report와 같다)."""
    from hri.prompt.summary_prompt import merge_reports

    return merge_reports(report_ids)

def _report_titles() -> dict[int, str]:
    """report_id -> 제목.
    목록을 못 가져와도 요약은 되게 빈 dict를 돌려준다."""
    try:
        return {r.report_id: r.title for r in search_reports()}
    except Exception:
        logger.exception("보고서 제목 조회 실패")
        return {}

def _escape_tilde(text: str) -> str:
    """Markdown에서 ~가 취소선으로 해석되지 않게 \\~로 바꾼다 (예: '2~3%')"""
    return (text or "").replace("~", "\\~")

def _page_label(start: int | None, end: int | None) -> str:
    """쪽 범위 문구.
    예) 'p.1-3', 'p.4', 쪽 정보가 없으면 ''"""
    if start is None:
        return ""
    if end is None or end == start:
        return f"p.{start}"
    return f"p.{start}-{end}"

def _summary_markdown(title: str, summary, view: str = VIEW_THREE_LINES) -> str:
    """보고서 1건의 요약(ReportSummary) 중 고른 방식(3줄 요약 / 섹션별 요약)만 Markdown으로 바꾼다"""
    lines = [f"### {_escape_tilde(title)}"]

    if view == VIEW_SECTIONS:
        lines.append("**섹션별 요약**")
        for s in summary.sections:
            pages = _page_label(s.start_page, s.end_page)
            heading = f"**{_escape_tilde(s.title)}**" + (f"{pages}" if pages else "")
            lines.append(f"- {heading}: {_escape_tilde(s.summary)}")
    else:
        lines.append("**3줄 요약**")
        lines += [f"{i}. {_escape_tilde(line)}" for i, line in enumerate(summary.three_lines, 1)]

    return "\n".join(lines)

def _merge_markdown(titles: list[str], merged, notes: list[str] | None = None) -> str:
    """병합 결과(MergedReport)를 공통 의견, 차이점, 종합 결론 Markdown으로 바꾼다."""
    lines = ["### 병합 결과", f"**대상 보고서**: {_escape_tilde(', '.join(titles))}"]
    lines += notes or []
    lines += ["", "**공통 의견**"]
    lines += [f"- {_escape_tilde(item)}" for item in merged.common] or ["- (공통 의견 없음)"]
    lines += ["", "**차이점**"]
    lines += [f"- {_escape_tilde(item)}" for item in merged.differences] or ["- (뚜렷한 차이 없음)"]
    lines += ["", "**종합 결론**", _escape_tilde(merged.conclusion)]

    return "\n".join(lines)

def build_summary_tab():
    """요약, 분석 탭 화면을 만든다.
    (gr.Blocks의 gr.Tabs 안에서 호출)
    맨 위에 보고서 선택(여러 개 가능)을 두고, 아래를 둘로 나눈다.
    - 왼쪽: 요약 버튼과 요약 결과(3줄 요약, 섹션별 요약)
    - 오른쪽: 3줄 요약과 섹션별 요약 라디오 버튼, 요청 실행 버튼과 분석 결과
    탭은 로그인 전에는 숨겨 둔 채(visible=False) 만들고, 로그인하면 on_login이 보이게 한다.
    이벤트 연결은 gr.State가 필요해서 build_app()이 connect_summary_events(state)로 한다.
    
    반환: gr.Tab (컴포넌트는 summary_components로 꺼내 쓴다)
    """
    with gr.Tab("요약, 분석", id=SUMMARY_TAB_ID, visible=False) as tab:
        report_dropdown = gr.Dropdown(label="보고서 선택 (여러 개 가능)", choices=[], multiselect=True)
        with gr.Row():
            with gr.Column(scale=1):
                view_radio = gr.Radio(label="요약 방식", choices=VIEW_CHOICES, value=VIEW_THREE_LINES)
            with gr.Column(scale=1):
                summarize_btn = gr.Button("요약하기", variant="primary")
                summary_result = gr.Markdown()
            with gr.Column():
                merge_btn = gr.Button("병합하기 (2개 이상 선택)")
                merge_result = gr.Markdown()

    summary_components.update(
        tab=tab,
        report_dropdown=report_dropdown,
        summarize_btn=summarize_btn,
        summary_result=summary_result,
        view_radio=view_radio,
        merge_btn=merge_btn,
        merge_result=merge_result,
    )
    return tab

@safe_call
def on_summarize(report_ids: list[int], view: str = VIEW_THREE_LINES) -> str:
    """요약하기 버튼: 선택한 보고서마다 summarize_report()를 호출해 Markdown으로 보여 준다.
    캐시가 있는 보고서는 LLM을 부르지 않고 바로 나온다.
    한 보고서가 실패해도(OutputFormatError 등) 그 보고서 자리에만 안내 문구를 넣고 나머지는 계속한다.

    입력: report_ids (선택한 보고서 번호 목록), view (요약 방식 라디오 값: '3줄 요약' 또는 '섹션별 요약')
    출력: str (화면에 보여 줄 Markdown)
    """
    if not report_ids:
        return NEED_REPORT_MESSAGE

    if view not in VIEW_CHOICES:
        view = VIEW_THREE_LINES
    titles = _report_titles()
    parts = []

    for report_id in report_ids:
        title = titles.get(report_id, f"보고서 {report_id}")

        try:
            summary = _summarize_report(report_id)
        except OutputFormatError:
            parts.append(f"### {title}\n{SUMMARY_FAIL_MESSAGE}")
        except Exception:
            logger.exception("요약 실패: report_id=%s", report_id)
            parts.append(f"### {title}\n{SUMMARY_ERROR_MESSAGE}")
        else:
            parts.append(_summary_markdown(title, summary, view))
    return "\n\n---\n\n".join(parts)

@safe_call
def on_merge(report_ids: list[int]) -> str:
    """병합하기 버튼: 선택한 보고서 2~5개를 merge_reports()로 병합해 공통 의견·차이점·결론을 보여 준다. (제너레이터)
    
    순서
    1. 2개 미만이거나 MAX_MERGE_REPORTS개를 넘으면 안내만 보여준다
    2. 보고서마다 요약을 먼저 준비하며 '(n/전체)' 진행 상황을 보여 준다 (캐시가 있으면 LLM 호출 없음)
        한 보고서라도 요약에 실패하면 그 보고서 이름과 사유를 보여 주고 병합하지 않는다
    3. merge_reports()로 병합한다 (2단계에서 요약이 저장돼 있어 요약을 다시 만들지 않는다)
    4. 같은 제목의 보고서가 있으면 결과 위에 경고를 붙인다

    입력: report_ids (선택한 보고서 번호 목록)
    yield 값: str (진행 상황 또는 결과 Markdown)
    """
    ids = list(dict.fromkeys(report_ids or []))

    if len(ids) < 2:
        yield NEED_MERGE_MESSAGE
        return
    if len(ids) > MAX_MERGE_REPORTS:
        yield MERGE_TOO_MANY_MESSAGE.format(max=MAX_MERGE_REPORTS)
        return
    titles = _report_titles()
    names = [titles.get(i, f"보고서 {i}") for i in ids]

    # 2. 보고서마다 요약 준비
    for no, (report_id, name) in enumerate(zip(ids, names), 1):
        yield f"⏳ 요약 준비 중 ({no}/{len(ids)}): {name}"
        try:
            _summarize_report(report_id)
        except OutputFormatError:
            yield f"### 병합하지 못했습니다\n**{name}**: {SUMMARY_FAIL_MESSAGE}"
            return
        except Exception:
            logger.exception("병합 전 요약 실패: report_id=%s", report_id)
            yield f"### 병합하지 못했습니다\n**{name}**: {SUMMARY_ERROR_MESSAGE}"
            return

    # 3. 병합
    yield f"⏳ 병합 중 ({len(ids)}개 보고서)"
    try:
        merged = _merge_reports(ids)
    except OutputFormatError:
        yield MERGE_FAIL_MESSAGE
        return
    except Exception:
        logger.exception("병합 실패: report_ids=%s", ids)
        yield MERGE_ERROR_MESSAGE
        return
    notes = [MERGE_DUPLICATE_NOTE] if len(set(names)) < len(names) else []
    yield _merge_markdown(names, merged, notes)