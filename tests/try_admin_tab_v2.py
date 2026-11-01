"""관리자 대시보드 2차 화면 테스트 (임세준) - 커밋하지 않는 파일

진짜 build_admin_tab(), on_refresh(), plot_daily_counts()는 그대로 쓰고,
DB가 필요한 get_dashboard_data_v2()만 가짜 DashboardData를 돌려주게 바꾼다.
1부: 함수 확인 (✅/❌) → 2부: 화면을 띄워서 새로고침 버튼 직접 눌러 보기

실행 (레포 맨 위 폴더에서):
	poetry run python -m tests.try_admin_tab_v2
	→ 브라우저에서 http://127.0.0.1:7860 (끝내려면 터미널에서 Ctrl+C)
"""
from datetime import date

import gradio as gr

from hri.domain.schemas import DashboardData
from hri.view import admin_view
from hri.view.admin_view import build_admin_tab, on_refresh


# ----------------------------------------------------------------
# 1. 가짜 get_dashboard_data_v2 (DB 대신, 차트는 진짜로 그림)
# ----------------------------------------------------------------
CHART_PATH = admin_view.plot_daily_counts([
	{"day": date(2026, 10, 27), "question_count": 2, "avg_response_ms": 1500.0},
	{"day": date(2026, 10, 28), "question_count": 4, "avg_response_ms": 1300.0},
	{"day": date(2026, 10, 29), "question_count": 3, "avg_response_ms": 900.0},
])


def make_data(with_records):
	"""with_records=True면 기록이 있는 DB, False면 기록이 없는 DB처럼 만든 가짜 데이터"""
	if with_records:
		return DashboardData(
			top_questions="| 질문 | 횟수 |\n|---|---|\n| 반도체 수출? | 3 |\n| 기준금리? | 2 |",
			source_ratio=0.75,
			avg_response_ms=1233.3,
			usage_by_flow="| flow | 입력 토큰 | 출력 토큰 | 원화 비용 |\n|---|---|---|---|\n| qa | 1200 | 300 | 1.26원 |",
			cost_krw=1.26,
			reports="| 제목 | 발행일 | 청크 수 |\n|---|---|---|\n| 2027 경제 전망 | 2026-10-01 | 42 |",
			recent_errors="| 시각 | 사용자 | 질문 | 에러 |\n|---|---|---|---|\n| 2026-10-29 10:05:00 | kim |  | 빈 질문 |",
			daily_chart=CHART_PATH,
			persona_ratio="| 페르소나 | 질문 수 | 비율 |\n|---|---|---|\n| 실무자 | 6 | 66.7% |\n| 입문자 | 3 | 33.3% |\n| 합계 | 9 |  |",
			feedback_ratio="| 구분 | 건수 | 비율 |\n|---|---|---|\n| 👍 | 2 | 66.7% |\n| 👎 | 1 | 33.3% |\n| 합계 | 3 |  |",
			no_source_questions="| 질문 | '자료에 없습니다' 횟수 |\n|---|---|\n| 반도체 수출? | 3 |\n| 부동산? | 2 |"
		)
	return DashboardData(
		top_questions="| 질문 | 횟수 |\n|---|---|",
		source_ratio=0.0,
		avg_response_ms=0.0,
		usage_by_flow="| flow | 입력 토큰 | 출력 토큰 | 원화 비용 |\n|---|---|---|---|",
		cost_krw=0.0,
		reports="| 제목 | 발행일 | 청크 수 |\n|---|---|---|",
		recent_errors="| 시각 | 사용자 | 질문 | 에러 |\n|---|---|---|---|",
		daily_chart=None,
		persona_ratio="| 페르소나 | 질문 수 | 비율 |\n|---|---|---|",
		feedback_ratio="| 구분 | 건수 | 비율 |\n|---|---|---|",
		no_source_questions="| 질문 | '자료에 없습니다' 횟수 |\n|---|---|"
	)


# 2부 화면의 체크박스로 바꿀 수 있게 전역 변수로 둠
fake = {"with_records": True}
admin_view.get_dashboard_data_v2 = lambda: make_data(fake["with_records"])

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
# 3. 1부: 함수 확인
# ----------------------------------------------------------------
with gr.Blocks() as demo:
	with gr.Tabs():
		admin_tab = build_admin_tab()

check("1 build_admin_tab → gr.Tab, id='admin', 처음엔 숨김", isinstance(admin_tab, gr.Tab) and admin_tab.id == "admin" and admin_tab.visible == False)

markdowns = 0
images = []
for block in demo.blocks.values():
	if isinstance(block, gr.Markdown):
		markdowns = markdowns + 1
	if isinstance(block, gr.Image):
		images.append(block)
check("2 화면 구성: gr.Markdown 15개(제목 5 + 값 10)·gr.Image 1개", markdowns == 15 and len(images) == 1, f"Markdown {markdowns}, Image {len(images)}")

message_before = False
for block in demo.blocks.values():
	if isinstance(block, gr.Markdown) and "표시할 데이터가 없습니다" in str(block.value):
		message_before = True
check("3 새로고침 전: 차트 칸 숨김, 안내 문구도 안 보임", images[0].visible == False and message_before == False)

# 기록이 있을 때
fake["with_records"] = True
result = on_refresh()
check("4 on_refresh → tuple 11개", isinstance(result, tuple) and len(result) == 11, len(result))
check("5 차트가 있으면: 이미지 칸 보이고 차트 경로가 들어감", result[1]["visible"] == True and result[1]["value"] == CHART_PATH)
check("6 차트가 있으면: 안내 문구는 빈칸", result[2] == "")
data = make_data(True)
check("7 2차 표 3개(페르소나·피드백·근거 없는 질문)가 제자리에", result[3] == data.persona_ratio and result[5] == data.feedback_ratio and result[6] == data.no_source_questions)
check("8 1차 값도 그대로 (TOP 10·비율 %·응답 시간·표)", result[0] == data.top_questions and "75.0%" in result[4] and "1233.3 ms" in result[7] and result[10] == data.recent_errors)

# 기록이 없을 때
fake["with_records"] = False
result = on_refresh()
check("9 차트가 없으면: 이미지 칸 숨김", result[1]["visible"] == False and result[1]["value"] == None)
check("10 차트가 없으면: '일별 질문 수: 표시할 데이터가 없습니다.'", result[2] == "일별 질문 수: 표시할 데이터가 없습니다.", result[2])


def broken():
	raise RuntimeError("DB 연결 실패")


admin_view.get_dashboard_data_v2 = broken
try:
	on_refresh()
	check("11 에러 나면 safe_call이 gr.Error로 바꿈", False)
except gr.Error as e:
	check("11 에러 나면 safe_call이 gr.Error로 바꿈", True, e.message)
admin_view.get_dashboard_data_v2 = lambda: make_data(fake["with_records"])

print(f"\n통과 {passed}/{total}")


# ----------------------------------------------------------------
# 4. 2부: 화면 띄우기 (관리자로 로그인한 것처럼 관리자 탭을 보이게 함)
# ----------------------------------------------------------------
def open_for_test():
	"""테스트용: 관리자 탭 보이게 하기"""
	fake["with_records"] = True
	return gr.update(visible=True)


def set_empty(empty):
	"""테스트용: 체크하면 기록이 없는 DB처럼 바꾸기 (체크 후 새로고침을 누르세요)"""
	fake["with_records"] = not empty


with gr.Blocks() as screen:
	empty_box = gr.Checkbox(label="(테스트용) 기록이 없는 DB처럼 보기 → 체크한 뒤 새로고침", value=False)
	with gr.Tabs():
		screen_tab = build_admin_tab()

	empty_box.change(fn=set_empty, inputs=empty_box)
	screen.load(fn=open_for_test, outputs=screen_tab)

screen.launch(theme=gr.themes.Soft())