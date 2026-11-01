"""출처 정리 · format_sources (장유빈)"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from hri.domain.schemas.qa import ContextDoc


# =====================================================================
# [장유빈] 출처 정리 · format_sources
# 답변에 쓴 근거에서 보고서명·페이지·섹션 출처 목록을 만든다 (중복 제거·정렬)
# =====================================================================
def format_sources(docs: list[ContextDoc]) -> list[dict]:
	"""근거(ContextDoc) 목록에서 출처 목록(dict)을 만든다.

	같은 보고서·쪽수·종류·표시 이름이 겹치면 하나로 합치고, 보고서 번호와 쪽수 순으로 정렬한다.
	차트(kind='chart')는 '그림 n(차트)'로 표시하고, needs_review=True인 출처는 '(추정값)'을 덧붙인다.
	각 출처는 report_id, report_title, page_start, page_end, kind, label, needs_review를 담은 dict이며,
	Markdown 변환은 chat_view가 한다. 근거가 없으면 빈 리스트를 돌려준다.
	"""
	# key(같은 출처인지 판단하는 값) → 출처 dict. 같은 key가 또 나오면 합친다
	sources = {}
	for doc in docs:
		# 차트는 '그림 n(차트)', 그 밖(글·표)은 섹션 제목을 표시 이름으로 쓴다
		if doc.kind == "chart":
			label = f"{doc.caption or '그림'}(차트)"
		else:
			label = doc.section_title or ""

		key = (doc.report_id, doc.page_start, doc.page_end, doc.kind, label)
		if key in sources:
			# 같은 출처가 또 나왔다: 하나로 합치되 추정값이 하나라도 있으면 표시를 남긴다
			sources[key]["needs_review"] = sources[key]["needs_review"] or doc.needs_review
		else:
			sources[key] = {
				"report_id": doc.report_id,
				"report_title": doc.report_title,
				"page_start": doc.page_start,
				"page_end": doc.page_end,
				"kind": doc.kind,
				"label": label,
				"needs_review": doc.needs_review,
			}

	# 보고서 번호 → 시작 쪽 → 끝 쪽 → 표시 이름 순으로 정렬한다
	result = sorted(
		sources.values(),
		key=lambda s: (s["report_id"], s["page_start"], s["page_end"], s["label"]),
	)

	# 정렬이 끝난 뒤에 추정값 표시를 붙인다 (정렬 기준이 바뀌지 않게 마지막에 붙인다)
	for source in result:
		if source["needs_review"]:
			source["label"] += " (추정값)"
	return result