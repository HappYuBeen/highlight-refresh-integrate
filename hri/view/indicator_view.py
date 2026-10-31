"""환율 패널·지표 비교 · build_indicator_panel (류현수) / on_compare (장유빈)"""
import logging

import gradio as gr

from hri.service.indicator_service import build_compare_table, build_ecos_reference, plot_indicators
from hri.service.report_service import owner_filter, search_reports

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# [장유빈] 지표 비교 · on_compare
# ---------------------------------------------------------------------
MIN_COMPARE_REPORTS = 2
MAX_COMPARE_REPORTS = 5  # 한 번에 비교할 수 있는 보고서 수 (COMPARE_TABLE_STYLE의 비활성화 선택자도 label.selected 5개 기준)
PICK_LABEL = f"보고서 선택 (최대 {MAX_COMPARE_REPORTS}개)"  # 요약·분석, 질의응답 탭의 보고서 목록과 같은 라벨
NEED_MORE_REPORTS_MESSAGE = "보고서를 2개 이상 선택하세요."
NO_INDICATORS_MESSAGE = "선택한 보고서에서 비교할 지표를 찾지 못했습니다."
NO_CHART_NOTE = "공통으로 나온 지표가 없어 차트는 만들지 않았습니다."
COMPARE_ERROR_MESSAGE = "지표를 비교하는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
ECOS_PROGRESS_TEXT = "ECOS 통계를 확인하는 중..."
PICK_LIMIT_MESSAGE = f"보고서는 최대 {MAX_COMPARE_REPORTS}개까지 비교할 수 있습니다."

# 지표 비교표 탭 스타일
# - 왼쪽(보고서 선택 창)·오른쪽 칸 높이를 화면에 맞춘다
# - 왼쪽 보고서 목록은 요약·분석, 질의응답 탭과 같은 모양이다 (summary_view.SUMMARY_TAB_STYLE의 보고서 목록 부분과 맞출 것)
#   한 줄에 보고서 하나씩, 흰색·연한 회색 번갈아, 남는 높이를 모두 쓰고 넘치면 목록 안에서 스크롤,
#   최대 개수만큼 고르면 나머지 보고서는 흐리게 하고 누를 수 없게 한다. 비교 버튼은 목록 아래 수평선 뒤에 붙는다
# - 오른쪽은 펼친 칸(비교 차트·비교 표·ECOS 데이터)끼리 남는 높이를 1/N씩 나누고, 넘치면 칸 안에서 스크롤한다
# - 표의 시점 열(2번째 열)은 'YYYYQN'(6글자)까지 한 줄로 보이게 자리를 잡는다
COMPARE_TABLE_STYLE = """
<style>
#compare-left,
#compare-right {
	height: calc(100vh - 12rem);
	min-height: 24rem;
	flex-wrap: nowrap;
}
#compare-left {
	overflow: hidden;
}
/* 왼쪽 칸 안의 제목·버튼 등은 내용 높이만 씀 (Gradio가 넣는 flex-grow 때문에 버튼이 커지는 것을 막음) */
#compare-left > * {
	flex: 0 0 auto !important;
}
/* 보고서 목록 칸: 남는 높이를 모두 쓰고 넘치면 이 안에서만 스크롤
   (보고서 목록(CheckboxGroup)은 Gradio가 Form으로 한 번 더 감싸서, 감싸는 칸(compare-report-body)에 높이를 준다) */
#compare-left > #compare-report-body {
	flex: 1 1 0 !important;
	min-height: 0;
	overflow-y: auto;
	flex-wrap: nowrap;
}
/* 보고서 목록: 한 줄에 보고서 하나씩 선택 박스와 함께 */
#compare-report-list [data-testid="checkbox-group"] {
	flex-direction: column;
	flex-wrap: nowrap;
	align-items: stretch;
	gap: 0;
}
/* 보고서 줄 배경: 선택 여부와 상관없이 흰색·연한 회색 번갈아, 글자는 항상 기본 글자색 */
#compare-report-list [data-testid="checkbox-group"] > label {
	border-radius: 0;
	background: var(--block-background-fill);
	color: var(--body-text-color);
}
#compare-report-list [data-testid="checkbox-group"] > label:nth-of-type(even) {
	background: var(--neutral-100);
}
.dark #compare-report-list [data-testid="checkbox-group"] > label:nth-of-type(even) {
	background: var(--neutral-700);
}
/* 보고서를 최대 개수(MAX_COMPARE_REPORTS, 5개)만큼 고르면 나머지 보고서는 흐리게 하고 누를 수 없게 함
   (Gradio가 고른 보고서 줄에 selected 클래스를 붙인다) */
#compare-report-list [data-testid="checkbox-group"]:has(> label.selected ~ label.selected ~ label.selected ~ label.selected ~ label.selected) > label:not(.selected) {
	opacity: 0.45;
	pointer-events: none;
}
/* 비교 버튼 줄: 위에 수평선을 두어 보고서 목록과 구분 */
#compare-actions {
	flex-wrap: nowrap;
	padding-top: 0.75rem;
	border-top: 1px solid var(--border-color-primary);
}
#compare-right .compare-fold {
	display: flex;
	flex-direction: column;
	flex: 0 0 auto;
	min-height: 0;
	width: 100%;
}
#compare-right .compare-fold:has(.label-wrap.open) {
	flex: 1 1 0;
}
#compare-right .compare-fold [data-testid="accordion-content"] {
	flex: 1 1 0;
	min-height: 0;
	overflow: auto;
}
.compare-table th:nth-child(2),
.compare-table td:nth-child(2) {
	min-width: 6ch;
	white-space: nowrap;
}
.compare-table .compare-grid td,
.compare-table .compare-grid th {
	overflow-wrap: anywhere;
}
/* 차트 없이 안내 문구만 있을 때: 남는 높이를 나눠 갖지 않고 문구 높이만 차지 */
/* 칸 안쪽도 내용 높이를 따르게 한다 (flex-basis 0이 남아 있으면 칸 높이가 0으로 접혀 문구가 안 보인다) */
#compare-right .compare-fold.compare-fold-fit:has(.label-wrap.open),
#compare-right .compare-fold.compare-fold-fit [data-testid="accordion-content"] {
	flex: 0 0 auto;
}
.compare-chart-note {
	text-align: center;
	padding: 0.5rem 0;
}
.compare-table-pager {
	display: flex;
	justify-content: center;
	align-items: center;
	gap: 0.75rem;
	padding-top: 0.5rem;
}
.compare-table-pager button {
	padding: 0.1rem 0.6rem;
	border: 1px solid var(--border-color-primary);
	border-radius: var(--radius-sm);
	background: var(--button-secondary-background-fill);
	color: var(--body-text-color);
	cursor: pointer;
}
.compare-table-pager button:disabled {
	opacity: 0.4;
	cursor: default;
}
</style>
"""

# 비교 표·ECOS 데이터 표를 칸 높이에 맞춰 페이지로 나누는 스크립트 (칸 높이는 브라우저에서만 알 수 있어서 화면에서 나눈다)
# - .compare-table 안의 표마다 표 아래에 ◀ 1 / N ▶ 를 붙이고, 칸(accordion-content)에 들어가는 줄 수만큼만 보여 준다
# - 칸 크기가 바뀌면(펼치기·접기, 다른 칸을 펼침, 창 크기) 다시 나눈다. 한 페이지에 다 들어가면 페이지 표시는 숨긴다
# - 표 내용이 바뀌면(다시 비교) Gradio가 표를 새로 그리므로 새 표에 다시 붙인다
COMPARE_TABLE_SCRIPT = """
<script>
(() => {
	const layout = (table) => {
		const box = table.closest('[data-testid="accordion-content"]');
		const body = table.tBodies[0];
		const pager = table._pager;
		if (!box || !body || !pager || box.clientHeight === 0) return;
		const rows = Array.from(body.rows);
		rows.forEach((row) => { row.style.display = ""; });
		pager.style.display = "";
		const style = getComputedStyle(box);
		const inner = box.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
		const content = box.firstElementChild ? box.firstElementChild.offsetHeight : box.scrollHeight;
		const room = inner - (content - body.offsetHeight);
		const pages = [];
		let start = 0, used = 0;
		rows.forEach((row, i) => {
			const h = row.offsetHeight;
			if (i > start && used + h > room) { pages.push([start, i]); start = i; used = 0; }
			used += h;
		});
		pages.push([start, rows.length]);
		if (pages.length <= 1) { pager.style.display = "none"; return; }
		const page = Math.min(table._page || 0, pages.length - 1);
		table._page = page;
		const [from, to] = pages[page];
		rows.forEach((row, i) => { row.style.display = i >= from && i < to ? "" : "none"; });
		pager._text.textContent = `${page + 1} / ${pages.length}`;
		pager._prev.disabled = page === 0;
		pager._next.disabled = page === pages.length - 1;
	};
	const setup = (table) => {
		if (table._pager) return;
		const pager = document.createElement("div");
		pager.className = "compare-table-pager";
		pager._prev = document.createElement("button");
		pager._prev.textContent = "◀";
		pager._text = document.createElement("span");
		pager._next = document.createElement("button");
		pager._next.textContent = "▶";
		pager.append(pager._prev, pager._text, pager._next);
		pager._prev.onclick = () => { table._page = (table._page || 0) - 1; layout(table); };
		pager._next.onclick = () => { table._page = (table._page || 0) + 1; layout(table); };
		table.after(pager);
		table._pager = pager;
		const box = table.closest('[data-testid="accordion-content"]');
		if (box) {
			const watcher = new ResizeObserver(() => table.isConnected ? layout(table) : watcher.disconnect());
			watcher.observe(box);
		}
		layout(table);
	};
	let queued = false;
	const scan = () => {
		queued = false;
		document.querySelectorAll(".compare-table table").forEach(setup);
	};
	new MutationObserver(() => {
		if (!queued) { queued = true; requestAnimationFrame(scan); }
	}).observe(document.documentElement, { childList: true, subtree: true });
})();
</script>
"""


def on_compare(report_ids: list[int] | None, progress=gr.Progress()) -> tuple[str, str, str | None, str]:
	"""선택한 보고서들의 지표를 비교해 (비교 표, ECOS 데이터, 차트 이미지 경로, 차트 안내 문구)를 돌려준다.

	보고서가 2개 미만이면 비교 표 자리에 안내 문구만 보여 준다. 2개 이상이면 build_compare_table()과 plot_indicators()를 부르고,
	처리 중에는 진행 표시를 한다. 지표가 없으면 안내 문구를 보여 준다. 공통 지표가 없어 차트를 못 그리면
	이미지 자리는 None, 차트 안내 문구는 NO_CHART_NOTE다 (비교 차트 칸에 보여 준다).
	처리 중 예외가 나면 오류 안내 문구를 보여 준다. ECOS 데이터·차트 안내 문구가 없으면 빈 문자열이다.
	"""
	ids = list(dict.fromkeys(int(i) for i in (report_ids or [])))
	if len(ids) < MIN_COMPARE_REPORTS:
		return NEED_MORE_REPORTS_MESSAGE, "", None, ""

	try:
		progress(0.1, desc="보고서에서 지표를 읽는 중...")
		table = build_compare_table(ids)
		if not table:
			return NO_INDICATORS_MESSAGE, "", None, ""
		progress(0.7, desc="차트를 그리는 중...")
		image = plot_indicators(ids)
	except Exception:
		logger.exception("지표 비교 중 오류")
		return COMPARE_ERROR_MESSAGE, "", None, ""

	progress(0.9, desc=ECOS_PROGRESS_TEXT)
	return table, _safe_ecos_reference(ids), image, NO_CHART_NOTE if image is None else ""


def on_compare_picked(pick: dict, progress=gr.Progress()):
	"""지표 추출·비교 버튼: 보고서 선택 창에서 고른 보고서로 on_compare()를 부른다.

	비교가 끝나면 비교 차트 칸을 펼친다. 차트가 없으면 이미지 칸은 숨기고(빈 이미지 칸이 보이지 않게) 안내 문구만 보여 주고,
	비교 차트 칸 높이도 문구에 맞게 줄인다(compare-fold-fit).
	출력: (비교 표, ECOS 데이터, 비교 차트 이미지, 비교 차트 안내 문구, 비교 차트 칸)
	"""
	table, ecos, image, note = on_compare((pick or {}).get("selected"), progress)
	if image is None and not note:
		note = table  # 지표 없음·오류·보고서 부족: 비교 표와 같은 안내를 비교 차트 칸에도 보여 준다 (빈 칸이 열리지 않게)
	fold_classes = ["compare-fold"] + (["compare-fold-fit"] if image is None else [])
	return (
		table,
		ecos,
		gr.update(value=image, visible=image is not None),
		gr.update(value=note, visible=bool(note)),
		gr.update(open=True, elem_classes=fold_classes),
	)


def _safe_ecos_reference(report_ids: list[int]) -> str:
	"""ECOS 참고 섹션을 만든다. 실패해도 비교표는 보여 줘야 하므로 오류는 기록만 하고 빈 문자열을 돌려준다."""
	try:
		return build_ecos_reference(report_ids)
	except Exception:
		logger.exception("ECOS 참고 섹션 생성 실패")
		return ""


# ---------------------------------------------------------------------
# [장유빈] 보고서 선택 창 · 전체 목록 + 선택 칸, 최대 MAX_COMPARE_REPORTS개
# pick(gr.State): {"reports": [(제목, report_id), ...], "selected": [report_id, ...] (고른 순서 = 비교 표 열 순서)}
# ---------------------------------------------------------------------
def new_pick() -> dict:
	"""보고서 선택 창의 처음 상태 (목록·선택 없음)."""
	return {"reports": [], "selected": []}


def _render_pick(pick: dict):
	"""pick 상태를 화면에 보여 줄 값으로 바꾼다.

	출력: (pick, 보고서 선택 칸)
	"""
	return pick, gr.update(choices=pick["reports"], value=list(pick["selected"]))


def load_compare_reports(state: dict, pick: dict | None):
	"""보고서 선택 창의 목록을 DB에서 다시 읽는다 (로그인 뒤·지표 비교표 탭을 열 때).

	일반 사용자는 본인이 올린 보고서만, 관리자는 전체를 보여 준다. 이미 고른 보고서 중 목록에 남아 있는 것은 그대로 둔다.
	목록을 못 읽으면 빈 목록을 보여 준다.
	"""
	pick = pick or new_pick()
	try:
		reports = search_reports("", user_id=owner_filter((state or {}).get("user")))
		pick["reports"] = [(r.title, r.report_id) for r in reports]
	except Exception:
		logger.exception("비교할 보고서 목록을 읽지 못했습니다")
		pick["reports"] = []
	existing = {rid for _, rid in pick["reports"]}
	pick["selected"] = [rid for rid in pick["selected"] if rid in existing]
	return _render_pick(pick)


def reset_compare_reports(state: dict):
	"""로그인 뒤: 선택을 비우고 목록을 보여 준다."""
	return load_compare_reports(state, new_pick())


def on_pick_change(value: list[int] | None, pick: dict):
	"""선택 칸을 누르면: 고른 보고서를 pick에 반영한다.

	고른 순서를 지켜 비교 표 열 순서로 쓴다. 화면에서는 5개를 고르면 나머지가 비활성화되지만(COMPARE_TABLE_STYLE),
	키보드로 고르는 경우를 위해 MAX_COMPARE_REPORTS개를 넘으면 이번 선택은 받지 않고 안내를 띄운다.
	"""
	value = [int(v) for v in (value or [])]
	selected = [rid for rid in pick["selected"] if rid in value]
	selected += [rid for rid in value if rid not in selected]
	if len(selected) > MAX_COMPARE_REPORTS:
		gr.Warning(PICK_LIMIT_MESSAGE)
	else:
		pick["selected"] = selected
	return _render_pick(pick)
