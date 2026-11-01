"""요약 데모·실제 LLM 테스트 (더미 보고서 포함, 한 파일)

- CLI 데모 : poetry run python -m tests.demo_summary
- pytest   : set RUN_LLM_TESTS=1 후 poetry run pytest -q tests\\demo_summary.py
  (RUN_LLM_TESTS가 1이 아니면 pytest에서는 건너뜀. 둘 다 실제 LLM을 부르므로 API 비용 발생)
"""
import os
import sys
import time

import pytest

from hri.domain.schemas import Section
from hri.prompt.summary_prompt import summarize_sections, summarize_three_lines

# ─────────────────────────────────────────────────────────────
# 더미 보고서 (경제 보고서 흉내, 섹션 3개)
# ─────────────────────────────────────────────────────────────
DUMMY_SECTIONS = [
	Section(
		section_no=0, title="Ⅰ. 최근 국내외 경제 동향", start_page=1, end_page=3,
		text=(
			"Ⅰ. 최근 국내외 경제 동향\n"
			"2025년 한국경제는 내수 회복 지연과 대외 불확실성으로 1.7% 성장하는 데 그칠 전망이다. "
			"민간소비는 금리 하락과 가계 가처분소득 확대로 소폭 회복될 것으로 보이나, "
			"건설투자는 부진이 이어지고 있다. 수출은 반도체가 견인하며 전년 대비 3.2% 증가했다. "
			"미국의 관세 정책 변화로 대외 여건의 불확실성은 높은 수준을 유지하고 있다."
		),
	),
	Section(
		section_no=1, title="Ⅱ. 물가와 환율", start_page=4, end_page=6,
		text=(
			"Ⅱ. 물가와 환율\n"
			"2025년 연간 소비자물가 상승률은 2.1%로 안정적이었다. "
			"그러나 원/달러 환율이 10% 상승하면 수입물가를 통해 소비자물가 상승률이 "
			"약 0.3~0.5%p 높아지는 것으로 분석되었다. 단기(3개월 이내)에는 약 2.4%, "
			"6개월 후에는 약 2.6%까지 오를 수 있다. 국제유가가 함께 오르는 복합 충격 국면에서는 "
			"상방 압력이 더 커질 가능성이 있다."
		),
	),
	Section(
		section_no=2, title="Ⅲ. 시사점", start_page=7, end_page=8,
		text=(
			"Ⅲ. 시사점\n"
			"첫째, 정부는 내수 진작을 위해 소비 여력 확충 정책을 병행해야 한다. "
			"둘째, 환율 변동이 물가에 전가되는 경로를 선제적으로 점검하고 "
			"취약 계층의 에너지·식료품 부담을 완화할 필요가 있다. "
			"셋째, 수출 시장 다변화로 특정 품목·지역 의존도를 낮춰야 한다."
		),
	),
]


def run_summary():
	"""섹션별 요약 → 3줄 요약을 실행해 (섹션 요약 목록, 3줄 요약)을 돌려준다."""
	summaries = []
	for section in DUMMY_SECTIONS:  # summary_graph가 Send로 섹션 1개씩 부르는 것과 같은 방식
		summaries += summarize_sections([section])
	return summaries, summarize_three_lines(summaries)


# ─────────────────────────────────────────────────────────────
# pytest (RUN_LLM_TESTS=1일 때만 실행)
# ─────────────────────────────────────────────────────────────
@pytest.mark.skipif(
	os.getenv("RUN_LLM_TESTS") != "1",
	reason="실제 LLM을 부르는 테스트입니다. 실행하려면 RUN_LLM_TESTS=1",
)
def test_더미_보고서를_섹션별로_요약하고_3줄로_줄인다():
	summaries, result = run_summary()

	assert [s.title for s in summaries] == [s.title for s in DUMMY_SECTIONS]
	assert [(s.start_page, s.end_page) for s in summaries] == [(1, 3), (4, 6), (7, 8)]
	assert all(s.summary.strip() for s in summaries)

	assert len(result.lines) == 3
	assert all(line.strip() for line in result.lines)


# ─────────────────────────────────────────────────────────────
# CLI 데모
# ─────────────────────────────────────────────────────────────
def main():
	sys.stdout.reconfigure(encoding="utf-8")
	start = time.perf_counter()

	print("=" * 60)
	print(f"입력: 더미 섹션 {len(DUMMY_SECTIONS)}개")
	for s in DUMMY_SECTIONS:
		print(f"  - [{s.section_no}] {s.title} (p.{s.start_page}-{s.end_page}, {len(s.text)}자)")

	summaries, result = run_summary()

	print("\n[1] 섹션별 요약 (summarize_sections)")
	print("-" * 60)
	for r in summaries:
		print(f"■ {r.title}  (p.{r.start_page}-{r.end_page})")
		print(f"  {r.summary}\n")

	print("[2] 3줄 요약 (summarize_three_lines)")
	print("-" * 60)
	for i, line in enumerate(result.lines, 1):
		print(f"  {i}. {line}  ({len(line)}자)")

	print("-" * 60)
	print(f"완료: {time.perf_counter() - start:.1f}초")


if __name__ == "__main__":
	main()