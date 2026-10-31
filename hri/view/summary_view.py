"""요약·분석 탭 · build_summary_tab, on_summarize (김희진)"""
import logging
import gradio as gr
from hri.util.logger import safe_call
from hri.exception.errors import OutputFormatError
from hri.service.report_service import search_reports

logger = logging.getLogger(__name__)

# app.py의 요약, 분석 탭 id (report_view.SUUMARY_TAB_ID와 같아야 한다)
MAX_MERGE_REPORTS = 5
# 보고서 목록에서 한 번에 고를 수 있는 보고서 수 (SUMMARY_TAB_STYLE의 비활성화 선택자도 label.selected 5개 기준)
MAX_SELECTED_REPORTS = 5
SELECT_LIMIT_MESSAGE = "보고서는 최대 {max}개까지 선택할 수 있습니다."
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
PAGE_LABEL = "**{no} / {total}**"

# 요약·분석 탭 배치 스타일 (head에 넣는 스타일, rem·vh만 씀)
# 탭 전체를 창 높이에 맞춰 페이지 전체는 스크롤되지 않게 하고,
# 아래쪽 자연어 요청 칸이 필요한 만큼 쓴 뒤 남는 높이를 왼쪽(보고서·버튼)·오른쪽(결과) 두 칸이 모두 쓴다.
# 결과가 길면 결과 칸 안에서만 스크롤된다.
SUMMARY_TAB_STYLE = """
<style>
#summary-body {
	height: calc(100vh - 6rem);
	overflow: hidden;
	flex-wrap: nowrap;
}
/* 위쪽 두 칸: 남는 높이를 모두 씀 (Gradio가 넣는 인라인 flex-grow를 덮어씀) */
#summary-main-row {
	flex: 1 1 0 !important;
	min-height: 0;
}
#summary-left-col,
#summary-result-col {
	height: 100%;
	min-height: 0;
	overflow: hidden;
	flex-wrap: nowrap;
}
/* 두 칸 안의 제목·버튼 등은 내용 높이만 씀 (Gradio가 넣는 flex-grow 때문에 남는 높이가 나눠져 버튼이 커지고 결과가 아래로 밀리는 것을 막음) */
#summary-left-col > *,
#summary-result-col > * {
	flex: 0 0 auto !important;
}
/* 보고서 목록 칸·결과: 남는 높이를 모두 쓰고 넘치면 이 안에서만 스크롤
   (보고서 목록(CheckboxGroup)은 Gradio가 Form으로 한 번 더 감싸서, 감싸는 칸(summary-report-body)에 높이를 준다) */
#summary-report-body,
#summary-result {
	flex: 1 1 0 !important;
	min-height: 0;
	overflow-y: auto;
}
#summary-report-body {
	flex-wrap: nowrap;
}
/* 보고서 목록: 한 줄에 보고서 하나씩 선택 박스와 함께 */
#summary-report-list [data-testid="checkbox-group"] {
	flex-direction: column;
	flex-wrap: nowrap;
	align-items: stretch;
	gap: 0;
}
/* 보고서 줄 배경: 선택 여부와 상관없이 흰색·연한 회색 번갈아, 글자는 항상 기본 글자색
   (Soft 테마는 background-fill-primary·secondary가 둘 다 neutral-50이라 직접 지정하고,
    고른 줄의 파란 배경·흰 글자(checkbox-label-*-selected)도 여기서 덮어씀) */
#summary-report-list [data-testid="checkbox-group"] > label {
	border-radius: 0;
	background: var(--block-background-fill);
	color: var(--body-text-color);
}
#summary-report-list [data-testid="checkbox-group"] > label:nth-of-type(even) {
	background: var(--neutral-100);
}
.dark #summary-report-list [data-testid="checkbox-group"] > label:nth-of-type(even) {
	background: var(--neutral-700);
}
/* 요약·병합 버튼 줄: 위에 수평선을 두어 보고서 목록과 구분하고, 버튼 3개를 한 줄에 */
#summary-actions {
	flex-wrap: nowrap;
	gap: 0.5rem;
	padding-top: 0.75rem;
	border-top: 1px solid var(--border-color-primary);
}
/* 보고서를 최대 개수(MAX_SELECTED_REPORTS, 5개)만큼 고르면 나머지 보고서는 흐리게 하고 누를 수 없게 함
   (Gradio가 고른 보고서 줄에 selected 클래스를 붙인다) */
#summary-report-list [data-testid="checkbox-group"]:has(> label.selected ~ label.selected ~ label.selected ~ label.selected ~ label.selected) > label:not(.selected) {
	opacity: 0.45;
	pointer-events: none;
}
/* 페이지 넘김 줄: 결과 칸 맨 아래에 붙음 */
#summary-pager {
	flex: 0 0 auto !important;
	align-items: center;
	justify-content: center;
}
#summary-page-label {
	text-align: center;
}
</style>
"""
# 결과 칸 스크롤을 맨 위로 올리는 스크립트 (페이지를 넘기거나 새 결과가 나올 때 이전 스크롤 위치가 남지 않게)
SCROLL_RESULT_TOP_JS = "() => { const el = document.querySelector('#summary-result'); if (el) el.scrollTop = 0; }"

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
    화면을 왼쪽(3)·오른쪽(6) 두 칸으로 나누고, 그 아래에 자연어 요청 칸을 둔다.
    - 왼쪽: 보고서 목록(선택 박스, 여러 개 가능), 칸 맨 아래에 3줄 요약하기·섹션별 요약하기·병합하기 버튼
    - 오른쪽: 결과 칸 하나에 요약·병합 결과를 보여 준다.
      여러 보고서를 요약하면 보고서 1건이 1페이지이고, 결과 칸 아래의 이전·다음 버튼으로 넘긴다 (1건이면 버튼 숨김).
      결과가 길면 결과 칸 안에서만 스크롤된다.
    - 아래: 자연어 요청과 요청 실행 버튼 (이후 삭제 예정). 필요한 만큼만 쓰고 남는 높이는 위 두 칸이 쓴다.
    탭은 사용자 탭 줄(gr.Tabs) 안에 보이게 만들고, 로그인 전에는 build_app()이 탭 줄을 통째로 숨긴다.
    이벤트 연결은 gr.State가 필요해서 build_app()에서 한다. 배치 스타일은 SUMMARY_TAB_STYLE(head에 넣음)

    반환: gr.Tab (컴포넌트는 summary_components로 꺼내 쓴다)
    """
    with gr.Tab("요약, 분석", id=SUMMARY_TAB_ID) as tab:
        with gr.Column(elem_id="summary-body"):
            with gr.Row(equal_height=True, elem_id="summary-main-row"):
                # 왼쪽: 보고서 목록(선택 박스)과 요약·병합 버튼. 목록이 남는 높이를 쓰고 버튼은 칸 맨 아래에 붙는다
                with gr.Column(scale=3, variant="panel", elem_id="summary-left-col"):
                    gr.Markdown("#### 보고서")
                    # 키 이름은 다른 화면·app.py와 맞추려고 report_dropdown 그대로 둔다 (선택지·값 갱신 방식은 Dropdown과 같다)
                    # 최대 MAX_SELECTED_REPORTS개까지 고를 수 있어서 전체 선택 박스는 두지 않는다
                    with gr.Column(elem_id="summary-report-body"):
                        report_dropdown = gr.CheckboxGroup(
                            label=f"보고서 선택 (최대 {MAX_SELECTED_REPORTS}개)", choices=[], elem_id="summary-report-list"
                        )
                    # 요약·병합 버튼 3개를 한 줄에 (위쪽 수평선으로 보고서 목록과 구분, SUMMARY_TAB_STYLE)
                    with gr.Row(elem_id="summary-actions"):
                        three_lines_btn = gr.Button("3줄 요약하기", variant="primary", size="sm", min_width=0)
                        sections_btn = gr.Button("섹션별 요약하기", variant="primary", size="sm", min_width=0)
                        merge_btn = gr.Button("병합하기", variant="primary", size="sm", min_width=0)
                # 오른쪽: 결과 (요약은 보고서마다 1페이지)
                with gr.Column(scale=6, variant="panel", elem_id="summary-result-col"):
                    gr.Markdown("#### 결과")
                    summary_result = gr.Markdown(elem_id="summary-result")
                    with gr.Row(visible=False, elem_id="summary-pager") as pager:
                        prev_btn = gr.Button("◀ 이전", size="sm", scale=0, min_width=80)
                        page_label = gr.Markdown(elem_id="summary-page-label")
                        next_btn = gr.Button("다음 ▶", size="sm", scale=0, min_width=80)
                    # 요약 결과 페이지 목록(보고서별 Markdown)과 지금 보고 있는 페이지 번호(0부터)
                    pages_state = gr.State([])
                    page_state = gr.State(0)

    summary_components.update(
        tab=tab,
        report_dropdown=report_dropdown,
        three_lines_btn=three_lines_btn,
        sections_btn=sections_btn,
        merge_btn=merge_btn,
        summary_result=summary_result,
        pager=pager,
        prev_btn=prev_btn,
        next_btn=next_btn,
        page_label=page_label,
        pages_state=pages_state,
        page_state=page_state,
        # 요약 결과·페이지를 바꾸는 이벤트들이 함께 쓰는 출력 목록 (_show_page의 반환 순서와 같다)
        page_outputs=[summary_result, pages_state, page_state, pager, page_label, prev_btn, next_btn],
    )
    return tab

def on_limit_reports(report_ids: list[int]):
    """보고서 목록 값이 바뀔 때: MAX_SELECTED_REPORTS개를 넘으면 앞에서부터 그 개수만 남기고 안내한다.
    화면에서는 5개를 고르면 나머지가 비활성화되지만(SUMMARY_TAB_STYLE), 키보드로 고르거나
    보고서 목록 화면에서 더 많이 골라 넘어온 경우를 위해 서버에서도 한 번 더 맞춘다.

    입력: report_ids (선택한 보고서 번호 목록)
    출력: 보고서 목록 갱신 (넘지 않으면 그대로)
    """
    report_ids = list(report_ids or [])
    if len(report_ids) <= MAX_SELECTED_REPORTS:
        return gr.update()

    gr.Info(SELECT_LIMIT_MESSAGE.format(max=MAX_SELECTED_REPORTS))
    return gr.update(value=report_ids[:MAX_SELECTED_REPORTS])

def _show_page(pages: list[str], index: int) -> tuple:
    """요약 결과 페이지 목록 중 index번째를 결과 칸에 보여 주는 값을 만든다.
    index는 0 ~ 마지막 페이지 사이로 맞추고, 페이지가 2개 이상일 때만 페이지 넘김 줄을 보인다.

    입력: pages (페이지별 Markdown 목록), index (보여 줄 페이지 번호, 0부터)
    출력: (결과 Markdown, 페이지 목록, 페이지 번호, 페이지 넘김 줄, 페이지 표시, 이전 버튼, 다음 버튼)
          (summary_components["page_outputs"] 순서)
    """
    if not pages:
        return "", [], 0, gr.update(visible=False), "", gr.update(interactive=False), gr.update(interactive=False)

    index = min(max(index, 0), len(pages) - 1)
    return (
        pages[index],
        pages,
        index,
        gr.update(visible=len(pages) > 1),
        PAGE_LABEL.format(no=index + 1, total=len(pages)),
        gr.update(interactive=index > 0),
        gr.update(interactive=index < len(pages) - 1),
    )

def _summary_pages(report_ids: list[int], view: str) -> list[str]:
    """선택한 보고서마다 summarize_report()를 호출해 보고서 1건당 Markdown 1페이지를 만든다.
    캐시가 있는 보고서는 LLM을 부르지 않고 바로 나온다.
    한 보고서가 실패해도(OutputFormatError 등) 그 보고서 페이지에만 안내 문구를 넣고 나머지는 계속한다.

    입력: report_ids (선택한 보고서 번호 목록), view ('3줄 요약' 또는 '섹션별 요약')
    출력: list[str] (보고서별 Markdown)
    """
    titles = _report_titles()
    pages = []

    for report_id in report_ids:
        title = titles.get(report_id, f"보고서 {report_id}")

        try:
            summary = _summarize_report(report_id)
        except OutputFormatError:
            pages.append(f"### {title}\n{SUMMARY_FAIL_MESSAGE}")
        except Exception:
            logger.exception("요약 실패: report_id=%s", report_id)
            pages.append(f"### {title}\n{SUMMARY_ERROR_MESSAGE}")
        else:
            pages.append(_summary_markdown(title, summary, view))
    return pages

@safe_call
def on_summarize(report_ids: list[int], view: str = VIEW_THREE_LINES) -> tuple:
    """요약 버튼: 선택한 보고서를 고른 방식으로 요약해 결과 칸에 첫 페이지를 보여 준다.
    보고서 1건이 1페이지이고, 2건 이상이면 이전·다음 버튼으로 넘겨 본다.

    입력: report_ids (선택한 보고서 번호 목록), view (요약 방식: '3줄 요약' 또는 '섹션별 요약')
    출력: _show_page()와 같은 값 7개 (summary_components["page_outputs"] 순서)
    """
    if not report_ids:
        return _show_page([NEED_REPORT_MESSAGE], 0)

    if view not in VIEW_CHOICES:
        view = VIEW_THREE_LINES
    return _show_page(_summary_pages(report_ids, view), 0)

def on_summarize_three_lines(report_ids: list[int]) -> tuple:
    """3줄 요약하기 버튼: on_summarize(report_ids, '3줄 요약')"""
    return on_summarize(report_ids, VIEW_THREE_LINES)

def on_summarize_sections(report_ids: list[int]) -> tuple:
    """섹션별 요약하기 버튼: on_summarize(report_ids, '섹션별 요약')"""
    return on_summarize(report_ids, VIEW_SECTIONS)

def on_prev_page(pages: list[str], index: int) -> tuple:
    """이전 버튼: 요약 결과의 앞 페이지를 보여 준다. 출력: _show_page()와 같은 값 7개"""
    return _show_page(pages, index - 1)

def on_next_page(pages: list[str], index: int) -> tuple:
    """다음 버튼: 요약 결과의 다음 페이지를 보여 준다. 출력: _show_page()와 같은 값 7개"""
    return _show_page(pages, index + 1)

def on_clear_pages() -> tuple:
    """병합하기 전에 요약 페이지를 비우고 페이지 넘김 줄을 숨긴다 (병합 결과는 같은 결과 칸에 한 페이지로 나온다).
    출력: _show_page()와 같은 값 7개
    """
    return _show_page([], 0)

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