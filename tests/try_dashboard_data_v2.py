"""대시보드 2차 데이터·추이 차트 테스트 (임세준) - 커밋하지 않는 파일

진짜 get_dashboard_data_v2(), plot_daily_counts()는 그대로 쓰고,
DB가 필요한 부분만 가짜로 바꾼다.
- get_dashboard_data(): 가짜 1차 DashboardData (1차는 3.7.8에서 이미 테스트함)
- select_daily_counts()·select_persona_ratio()·select_feedback_ratio()·select_no_source_questions():
  MySQL이 돌려주는 모양 그대로 가짜 결과 (ROLLUP 합계 줄, date 날짜 포함)
차트 PNG는 진짜로 그려서 data/plots/에 저장한다 (.gitignore 대상이라 커밋되지 않음)

실행 (레포 맨 위 폴더에서):
	poetry run python -m tests.try_dashboard_data_v2
"""
import os
from datetime import date

from hri.domain.schemas import DashboardData
from hri.view import admin_view


# ----------------------------------------------------------------
# 1. 가짜 데이터
# ----------------------------------------------------------------
FAKE_V1 = DashboardData(
	top_questions="| 질문 | 횟수 |\n|---|---|\n| 반도체 수출? | 3 |",
	source_ratio=0.75,
	avg_response_ms=1233.3,
	usage_by_flow="| flow | 입력 토큰 | 출력 토큰 | 원화 비용 |\n|---|---|---|---|",
	cost_krw=0.0,
	reports="| 제목 | 발행일 | 청크 수 |\n|---|---|---|",
	recent_errors="| 시각 | 사용자 | 질문 | 에러 |\n|---|---|---|---|"
)

DAILY = [
	{"day": date(2026, 10, 27), "question_count": 2, "avg_response_ms": 1500.0},
	{"day": date(2026, 10, 28), "question_count": 4, "avg_response_ms": 1300.0},
	{"day": date(2026, 10, 29), "question_count": 3, "avg_response_ms": None},
]
PERSONA = [
	{"persona": "실무자", "count": 6},
	{"persona": "입문자", "count": 3},
	{"persona": None, "count": 9},
]
FEEDBACK = {"up_count": 2, "down_count": 1, "total": 3, "up_ratio": 0.6667, "down_ratio": 0.3333}
NO_SOURCE = [
	{"question": "반도체 수출?", "count": 3},
	{"question": "부동산?", "count": 2},
]

EMPTY_FEEDBACK = {"up_count": 0, "down_count": 0, "total": 0, "up_ratio": 0.0, "down_ratio": 0.0}


def use_fake(daily, persona, feedback, no_source):
	"""admin_view가 부르는 함수들을 가짜로 바꾸기"""
	admin_view.get_dashboard_data = lambda: FAKE_V1.model_copy()
	admin_view.select_daily_counts = lambda: daily
	admin_view.select_persona_ratio = lambda: persona
	admin_view.select_feedback_ratio = lambda: feedback
	admin_view.select_no_source_questions = lambda: no_source


# ----------------------------------------------------------------
# 2. 테스트 함수
# ----------------------------------------------------------------
passed = 0
total = 0


def check(name, ok, info=""):
	global passed, total
	total = total + 1
	if ok:
		passed = passed + 1
		print(f"✅ {name} {info}")
	else:
		print(f"❌ {name} {info}")


# ----------------------------------------------------------------
# 3. plot_daily_counts 단독 확인
# ----------------------------------------------------------------
path = admin_view.plot_daily_counts(DAILY)
check("1 plot_daily_counts → 글자(str) 경로", isinstance(path, str), path)
check("2 PNG 파일이 실제로 저장됨 (data/plots/daily_….png)", os.path.exists(path) and path.endswith(".png") and "daily_" in path)
print("     → 이 경로의 PNG를 열어서 선 2개·한글 제목·축 이름을 눈으로 확인하세요")

one_day = admin_view.plot_daily_counts([{"day": date(2026, 10, 27), "question_count": 2, "avg_response_ms": 1500.0}])
check("3 날짜가 하루뿐이어도 에러 없이 저장", os.path.exists(one_day))

# ----------------------------------------------------------------
# 4. get_dashboard_data_v2 (기록이 있을 때)
# ----------------------------------------------------------------
use_fake(DAILY, PERSONA, FEEDBACK, NO_SOURCE)
data = admin_view.get_dashboard_data_v2()
print("\n----- 2차 칸 -----")
print(f"[daily_chart] {data.daily_chart}")
print(f"[persona_ratio]\n{data.persona_ratio}")
print(f"[feedback_ratio]\n{data.feedback_ratio}")
print(f"[no_source_questions]\n{data.no_source_questions}")
print("------------------\n")

check("4 DashboardData로 돌려줌", isinstance(data, DashboardData))
check("5 1차 칸은 get_dashboard_data() 값 그대로", data.top_questions == FAKE_V1.top_questions and data.source_ratio == 0.75)
check("6 차트 경로가 담기고 파일이 있음 (평균 응답 시간 None인 날도 에러 없음)", data.daily_chart != None and os.path.exists(data.daily_chart))
check("7 페르소나: 실무자 6 (66.7%), 입문자 3 (33.3%)", "| 실무자 | 6 | 66.7% |" in data.persona_ratio and "| 입문자 | 3 | 33.3% |" in data.persona_ratio)
check("8 페르소나: ROLLUP 합계 줄은 '합계'로 표시", "| 합계 | 9 |  |" in data.persona_ratio)
check("9 피드백: 👍 2 (66.7%), 👎 1 (33.3%), 합계 3", "| 👍 | 2 | 66.7% |" in data.feedback_ratio and "| 👎 | 1 | 33.3% |" in data.feedback_ratio and "| 합계 | 3 |  |" in data.feedback_ratio)
check("10 근거 없는 질문: 반도체 3, 부동산 2", "| 반도체 수출? | 3 |" in data.no_source_questions and "| 부동산? | 2 |" in data.no_source_questions)

# ----------------------------------------------------------------
# 5. get_dashboard_data_v2 (기록이 없을 때)
# ----------------------------------------------------------------
use_fake([], [], EMPTY_FEEDBACK, [])
empty = admin_view.get_dashboard_data_v2()
check("11 빈 DB: 차트는 그리지 않고 None", empty.daily_chart == None)
check("12 빈 DB: 표 3개는 머리글만", empty.persona_ratio.count("\n") == 1 and empty.feedback_ratio.count("\n") == 1 and empty.no_source_questions.count("\n") == 1)

print(f"\n통과 {passed}/{total}")