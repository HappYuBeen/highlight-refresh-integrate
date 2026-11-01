"""summarize_report() 데모: 더미 DB(메모리) + 실제 LLM (API 비용 발생, DB·Chroma 불필요)

보여주는 것
  1) 처음 호출  : 섹션 3개를 읽어 LLM으로 요약 → 캐시 저장 → 사용량 기록
  2) 두 번째 호출: 캐시에서 바로 반환 (LLM 호출 없음, 사용량 기록 없음)
  3) 섹션 없는 보고서: OutputFormatError

실행: 프로젝트 맨 위 폴더에서  poetry run python -m tests.demo_summarize_report
"""
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import hri.dao.report_dao as report_dao
import hri.service.summary_service as summary_service
from hri.domain.schemas import Section
from hri.exception.errors import OutputFormatError

# ─────────────────────────────────────────────────────────────
# 더미 보고서 (id=1)
# ─────────────────────────────────────────────────────────────
REPORT_ID = 1
DUMMY_SECTIONS = [
	Section(
		report_id=REPORT_ID, section_no=0, title="Ⅰ. 최근 국내외 경제 동향", start_page=1, end_page=3,
		text=(
			"Ⅰ. 최근 국내외 경제 동향\n"
			"2025년 한국경제는 내수 회복 지연과 대외 불확실성으로 1.7% 성장하는 데 그칠 전망이다. "
			"민간소비는 금리 하락과 가계 가처분소득 확대로 소폭 회복될 것으로 보이나, "
			"건설투자는 부진이 이어지고 있다. 수출은 반도체가 견인하며 전년 대비 3.2% 증가했다. "
			"미국의 관세 정책 변화로 대외 여건의 불확실성은 높은 수준을 유지하고 있다."
		),
	),
	Section(
		report_id=REPORT_ID, section_no=1, title="Ⅱ. 물가와 환율", start_page=4, end_page=6,
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
		report_id=REPORT_ID, section_no=2, title="Ⅲ. 시사점", start_page=7, end_page=8,
		text=(
			"Ⅲ. 시사점\n"
			"첫째, 정부는 내수 진작을 위해 소비 여력 확충 정책을 병행해야 한다. "
			"둘째, 환율 변동이 물가에 전가되는 경로를 선제적으로 점검하고 "
			"취약 계층의 에너지·식료품 부담을 완화할 필요가 있다. "
			"셋째, 수출 시장 다변화로 특정 품목·지역 의존도를 낮춰야 한다."
		),
	),
]

# ─────────────────────────────────────────────────────────────
# 더미 DB: DB 대신 메모리에 저장하도록 DAO 함수를 바꿔 끼운다
# ─────────────────────────────────────────────────────────────
summary_cache: dict = {}      # reports.summary_json 흉내
usage_rows: list = []         # llm_usage 흉내

report_dao.select_sections = lambda report_id: DUMMY_SECTIONS if report_id == REPORT_ID else []
report_dao.select_summary_json = lambda report_id: summary_cache.get(report_id)


def _save_summary(report_id, summary):
	summary_cache[report_id] = summary
	return 1


report_dao.update_summary_json = _save_summary
summary_service.insert_usage = usage_rows.append      # llm_usage INSERT 대신 목록에 담는다
summary_service.get_usd_krw = lambda: 1400.0          # 환율 API 호출 없이 고정값
# 비용은 .env의 MODEL_PRICES(모델별 단가)로 계산한다. 단가가 null이면 계산을 못 하고 비용은 '계산 불가'로 표시된다.


def show(summary) -> None:
	print("  [3줄 요약]")
	for i, line in enumerate(summary.three_lines, 1):
		print(f"   {i}. {line}")
	print("  [섹션별 요약]")
	for s in summary.sections:
		print(f"   ■ {s.title} (p.{s.start_page}-{s.end_page})")
		print(f"     {s.summary}")


def show_usage() -> None:
	if not usage_rows:
		print("  [사용량 기록] 없음 (AI를 부르지 않음)")
		return
	for u in usage_rows:
		cost = "계산 불가(단가 없음)" if u.cost_krw is None else f"{u.cost_krw}원"
		print(f"  [사용량 기록] flow={u.flow} ref_id={u.ref_id} model={u.model} "
		      f"입력={u.input_tokens} 출력={u.output_tokens} 비용={cost}")


print("=" * 64)
print("1) 처음 호출 (LLM으로 요약)")
print("-" * 64)
start = time.perf_counter()
first = summary_service.summarize_report(REPORT_ID)
print(f"  걸린 시간: {time.perf_counter() - start:.1f}초")
show(first)
show_usage()

print("\n" + "=" * 64)
print("2) 두 번째 호출 (캐시에서 반환, LLM 호출 없음)")
print("-" * 64)
usage_rows.clear()
start = time.perf_counter()
second = summary_service.summarize_report(REPORT_ID)
print(f"  걸린 시간: {time.perf_counter() - start:.2f}초")
print(f"  첫 호출과 3줄 요약 동일: {second.three_lines == first.three_lines}")
show_usage()

print("\n" + "=" * 64)
print("3) 섹션이 없는 보고서 (report_id=99)")
print("-" * 64)
try:
	summary_service.summarize_report(99)
except OutputFormatError as e:
	print(f"  OutputFormatError: {e}")
print("=" * 64)