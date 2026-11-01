"""보고서 요약 흐름 · build_summary_graph, fan_out_sections, route_after_cache, load_cache_node, load_sections_node, summarize_section_node, three_lines_node, save_node (엄정현)

[3.8.6] 요약 그래프의 노드·분기를 명세대로 채웠다 (LangGraph 워크플로우 명세서 build_summary_graph·fan_out_sections 행).
노드가 부르는 함수는 김희진 담당이다: select_sections()·update_summary_json() (dao.report_dao),
summarize_sections()·summarize_three_lines() (prompt.summary_prompt). 모두 함수 안에서 import한다
(report_dao는 DB 모듈을, summary_prompt는 LLM 모듈을 import할 때 불러오므로 그래프 파일 import만으로 필요해지지 않게 함).

흐름
	START → load_cache → route_after_cache
		├─ 캐시 있음(reports.summary_json) → END (LLM 호출 없음)
		└─ 없음 → load_sections → fan_out_sections
				├─ state['error'](섹션 0개) → END
				└─ 섹션마다 Send('summarize_section', {'section': s}) → summarize_section (섹션별 병렬, map)
	→ three_lines (section_summaries를 operator.add로 모은 뒤, reduce) → save → END
compile은 registry.get_summary_graph()가 한 번만 한다.

결과 읽기 (summarize_report): 캐시가 있든 없든 최종 state의 three_lines와 section_summaries(start_page 순으로
sort_section_summaries()로 정렬)로 ReportSummary를 만들 수 있다. 섹션 0개면 state['error']가 있다.
"""

import logging

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from hri.domain.schemas import ReportSummary, SectionSummary, SummaryState

logger = logging.getLogger(__name__)

# ── 명세서에 정해지지 않은 값 (정해지면 아래 값만 바꾼다) ────────────────────────────
# TODO(확인 필요): reports.summary_json을 읽는 DAO 함수 이름.
#   명세서에는 'load_cache 노드가 summary_json이 있으면 LLM 호출 없이 반환'만 있고, 읽는 함수가 없다
#   (report_dao에는 저장용 update_summary_json()만 있음). 2026-10-27 김희진에게 전달, 김희진이 report_dao에 구현 예정.
#   구현된 함수 이름이 다르면 이 값만 바꾼다.
#   기대하는 모양: (report_id: int) -> ReportSummary | dict | None  (없으면 None)
#   hri.dao.report_dao에 이 이름의 함수가 아직 없으면 캐시가 없는 것으로 보고 요약을 새로 만든다 (경고 로그 남김).
CACHE_READER_NAME = "select_summary_json"

# TODO(확인 필요): 섹션이 0개일 때 state['error']에 넣을 안내 문구. 명세서에는 'state['error'] 설정'만 있다.
NO_SECTIONS_ERROR = "요약할 섹션이 없습니다"


def sort_section_summaries(summaries: list[SectionSummary]) -> list[SectionSummary]:
	"""섹션 요약을 start_page 순으로 정렬한다 (명세서: 리듀서로 모인 뒤 start_page 순으로 정렬).

	Send로 병렬 실행한 summarize_section은 끝나는 순서가 정해지지 않으므로 모인 순서를 믿지 않는다.
	start_page가 없는(None) 요약은 맨 뒤에 둔다.
	"""
	return sorted(summaries, key=lambda s: (s.start_page is None, s.start_page or 0))


def load_cache_node(state: SummaryState) -> dict:
	"""reports.summary_json(요약 캐시)이 있으면 그 내용을 state에 담는다.

	- 캐시 있음: {'three_lines': [...], 'section_summaries': [...]} → route_after_cache가 END로 보냄 (LLM 호출 없음)
	- 캐시 없음: {} → load_sections로 감
	캐시를 담을 SummaryState 키가 따로 없어서, 새로 만들 때와 같은 키(three_lines·section_summaries)에 담는다.
	"""
	from hri.dao import report_dao

	reader = getattr(report_dao, CACHE_READER_NAME, None)
	if reader is None:
		logger.warning("report_dao.%s가 없어 요약 캐시를 확인하지 못했습니다 (새로 요약)", CACHE_READER_NAME)
		return {}

	cached = reader(state["report_id"])
	if not cached:
		return {}
	summary = cached if isinstance(cached, ReportSummary) else ReportSummary.model_validate(cached)
	return {"three_lines": summary.three_lines, "section_summaries": summary.sections}


def route_after_cache(state: SummaryState) -> str:
	"""load_cache 다음 갈 곳: 캐시에서 3줄 요약을 담았으면 END, 아니면 load_sections."""
	if state.get("three_lines"):
		return END
	return "load_sections"


def load_sections_node(state: SummaryState) -> dict:
	"""select_sections(report_id)로 섹션을 페이지 순으로 불러온다. 0개면 state['error']를 설정한다.

	라우팅 함수(fan_out_sections)는 state를 바꿀 수 없으므로 오류 기록은 이 노드가 한다 (명세서).
	"""
	from hri.dao.report_dao import select_sections

	sections = select_sections(state["report_id"])
	if not sections:
		return {"sections": [], "error": NO_SECTIONS_ERROR}
	return {"sections": sections}


def fan_out_sections(state: SummaryState) -> list[Send] | str:
	"""섹션마다 summarize_section 노드를 하나씩 보내 병렬로 요약한다 (map).

	- state['error']가 있으면(섹션 0개 오류, load_sections 노드가 설정) END를 돌려준다.
	  라우팅 함수는 state를 바꿀 수 없으므로 오류 기록은 load_sections 노드가 한다.
	- 아니면 state['sections']마다 Send('summarize_section', {'section': s})를 만든다.
	- 각 summarize_section이 돌려준 section_summaries는 operator.add 리듀서로 모인다 (reduce).
	"""
	if state.get("error"):
		return END
	return [Send("summarize_section", {"section": s}) for s in state.get("sections", [])]


def summarize_section_node(payload: dict) -> dict:
	"""섹션 1개를 summarize_sections([section])으로 요약한다.

	Send로 불리므로 입력은 SummaryState 전체가 아니라 {'section': Section}이다.
	돌려준 {'section_summaries': [...]}는 operator.add로 다른 섹션 결과와 합쳐진다.
	"""
	from hri.prompt.summary_prompt import summarize_sections

	return {"section_summaries": summarize_sections([payload["section"]])}


def three_lines_node(state: SummaryState) -> dict:
	"""모인 섹션 요약을 start_page 순으로 정렬해 summarize_three_lines()에 넘기고, 3줄(lines)만 state에 담는다."""
	from hri.prompt.summary_prompt import summarize_three_lines

	result = summarize_three_lines(sort_section_summaries(state["section_summaries"]))
	return {"three_lines": result.lines}


def save_node(state: SummaryState) -> dict:
	"""ReportSummary를 만들어 update_summary_json()으로 reports.summary_json에 저장한다 (다음부터 load_cache가 사용).

	state는 바꾸지 않는다 (section_summaries는 operator.add 키라 다시 돌려주면 중복된다).
	"""
	from hri.dao.report_dao import update_summary_json

	summary = ReportSummary(
		report_id=state["report_id"],
		three_lines=state["three_lines"],
		sections=sort_section_summaries(state["section_summaries"]),
	)
	update_summary_json(state["report_id"], summary)
	return {}


def build_summary_graph() -> StateGraph:
	"""요약 그래프를 노드·엣지까지 연결해 돌려준다 (compile 전 StateGraph)."""
	graph = StateGraph(SummaryState)

	graph.add_node("load_cache", load_cache_node)
	graph.add_node("load_sections", load_sections_node)
	graph.add_node("summarize_section", summarize_section_node)
	graph.add_node("three_lines", three_lines_node)
	graph.add_node("save", save_node)

	graph.add_edge(START, "load_cache")
	graph.add_conditional_edges("load_cache", route_after_cache, ["load_sections", END])
	graph.add_conditional_edges("load_sections", fan_out_sections, ["summarize_section", END])
	# Send로 나뉜 summarize_section이 모두 끝나면 three_lines가 한 번 실행된다
	graph.add_edge("summarize_section", "three_lines")
	graph.add_edge("three_lines", "save")
	graph.add_edge("save", END)
	return graph