"""ECOS 활용 데이터 판단(find_usable_data)을 실제 ECOS·OpenAI API로 돌려 보는 스크립트 (류현수) - 커밋하지 않는 파일

.env의 ECOS_API_KEY·OPENAI_API_KEY를 쓰고, MySQL·Chroma는 필요 없다 (설정 값만 있으면 된다).
처음 실행하면 통계표 목록을 받아 data/ecos/에 캐시하므로 조금 걸린다. 다시 받으려면 --refresh.

실행 (레포 맨 위 폴더에서):
	poetry run python -m tests.try_ecos_finder
	poetry run python -m tests.try_ecos_finder --refresh
"""
import logging
import sys
import time

from hri.api import ecos_finder
from hri.api.ecos_client import fetch_indicators
from hri.domain.schemas import Indicator

# 보고서에서 뽑혔다고 치는 지표 (값은 예시). 이 중 가장 연관된 지표 1개 + ECOS 통계 1개가 골라진다
# (2027 전망치, ECOS에 없는 지표는 골라지면 안 된다)
SAMPLES = [
	Indicator(name="경제성장률", value=0.6, unit="%", period="2026Q2"),
	Indicator(name="경제성장률", value=0.2, unit="%", period="2026Q1"),
	Indicator(name="소비자물가 상승률", value=2.1, unit="%", period="202609"),
	Indicator(name="실업률", value=2.8, unit="%", period="202608"),
	Indicator(name="기준금리", value=2.5, unit="%", period="202609"),
	Indicator(name="원/달러 환율", value=1385.5, unit="원", period="202609"),
	Indicator(name="경상수지", value=101.5, unit="억 달러", period="202608"),
	Indicator(name="경제성장률", value=1.8, unit="%", period="2027"),
	Indicator(name="유튜브 조회수", value=1000, unit="회", period="202609"),
]


def main():
	logging.basicConfig(level=logging.INFO, format="  [%(levelname)s] %(message)s")
	logging.getLogger("httpx").setLevel(logging.WARNING)

	started = time.time()
	tables = ecos_finder.load_table_list(refresh="--refresh" in sys.argv)
	print(f"통계표 목록: {len(tables)}개 ({time.time() - started:.1f}초)\n")

	started = time.time()
	query = ecos_finder.find_usable_data(SAMPLES)
	print(f"\nfind_usable_data: {time.time() - started:.1f}초\n")

	if query is None:
		print("→ 비교할 ECOS 통계 없음")
		return
	picked = [i for i in SAMPLES if i.name == query.indicator_name and i.period.upper() == query.start]
	print(f"■ 고른 지표: {query.indicator_name} ({query.start})", *(f"{i.value}{i.unit}" for i in picked))
	print(f"  → {query.stat_code} / {query.item_code} / {query.item_code2} ({query.cycle}) : {query.reason}")
	rows = fetch_indicators(query, query.indicator_name)
	for row in rows[:4]:
		print(f"     ECOS 값: {row.value}{row.unit} ({row.period})")
	if not rows:
		print("     ECOS 값: 없음 (조회 결과가 비었음)")

if __name__ == "__main__":
	main()
