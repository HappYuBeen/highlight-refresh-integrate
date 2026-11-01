"""관리자 대시보드 화면 테스트 (임세준) - 커밋하지 않는 파일

진짜 build_admin_tab(), on_refresh()는 그대로 쓰고,
DB가 필요한 get_dashboard_data()만 가짜 DashboardData를 돌려주게 바꾼다.
1부: 함수 확인 (✅/❌) → 2부: 화면을 띄워서 새로고침 버튼 직접 눌러 보기
(리뷰 반영: 차트가 없으면 이미지 칸 대신 '표시할 데이터가 없습니다' 안내 문구가 보이는지도 확인)

실행 (레포 맨 위 폴더에서):
	poetry run python -m tests.try_admin_tab
	→ 브라우저에서 http://127.0.0.1:7860 (끝내려면 터미널에서 Ctrl+C)
"""
import gradio as gr

from hri.domain.schemas import DashboardData
from hri.view import admin_view
from hri.view.admin_view import build_admin_tab, on_refresh


# ----------------------------------------------------------------
# 1. 가짜 get_dashboard_data (DB 대신)
# ----------------------------------------------------------------
FAKE_DATA = DashboardData(
	top_questions="| 질문 | 횟수 |\n|---|---|\n| 기준금리 전망은? | 5 |\n| 환율은? | 3 |",
	source_ratio=0.0455,
	avg_response_ms=1159.1,
	usage_by_flow="| flow | 입력 토큰 | 출력 토큰 | 원화 비용 |\n|---|---|---|---|\n| qa | 1200 | 300 | 1.26원 |\n| ingest | 100000 | 20000 | 1540.0원 |",
	cost_krw=1542.73,
	reports="| 제목 | 발행일 | 청크 수 |\n|---|---|---|\n| 2027 경제 전망 | 2026-10-01 | 42 |",
	recent_errors="| 시각 | 사용자 | 질문 | 에러 |\n|---|---|---|---|\n| 2026-10-28 10:00:30 | kim |  | 빈 질문 |"
)


def fake_get_dashboard_data():
	print("[가짜] get_dashboard_data() 호출")
	return FAKE_DATA


admin_view.get_dashboard_data = fake_get_dashboard_data

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

check("1 build_admin_tab → gr.Tab", isinstance(admin_tab, gr.Tab), type(admin_tab).__name__)
check("2 탭 id='admin', 처음엔 숨김 (관리자만 보이게)", admin_tab.id == "admin" and admin_tab.visible == False)

markdowns = 0
images = 0
buttons = 0
for block in demo.blocks.values():
	if isinstance(block, gr.Markdown):
		markdowns = markdowns + 1
	if isinstance(block, gr.Image):
		images = images + 1
	if isinstance(block, gr.Button) and block.value == "새로고침":
		buttons = buttons + 1
check("3 화면 구성: gr.Markdown(제목 5 + 값 6 + 안내 1)·gr.Image 1·새로고침 버튼 1", markdowns == 12 and images == 1 and buttons == 1, f"Markdown {markdowns}, Image {images}, 버튼 {buttons}")

# 리뷰 반영: 차트가 없으면 이미지 칸은 숨기고 안내 문구를 보여 줌
chart = None
chart_message = None
for block in demo.blocks.values():
	if isinstance(block, gr.Image):
		chart = block
	if isinstance(block, gr.Markdown) and block.value == "일별 질문 수: 표시할 데이터가 없습니다.":
		chart_message = block
check("4 차트가 없으면 이미지 칸은 숨기고 안내 문구 표시", chart.visible == False and chart_message != None and chart_message.visible == True)

result = on_refresh()
check("5 on_refresh → tuple 6개", isinstance(result, tuple) and len(result) == 6, len(result))
check("6 표 4개는 get_dashboard_data() 값 그대로", result[0] == FAKE_DATA.top_questions and result[3] == FAKE_DATA.usage_by_flow and result[4] == FAKE_DATA.reports and result[5] == FAKE_DATA.recent_errors)
check("7 자료 유무 비율은 %로", "4.5%" in result[1], result[1])
check("8 응답 시간은 ms, 원화 합계는 원으로", "1159.1 ms" in result[2] and "1542.73원" in result[2], result[2])


def broken_get_dashboard_data():
	raise RuntimeError("DB 연결 실패")


admin_view.get_dashboard_data = broken_get_dashboard_data
try:
	on_refresh()
	check("9 에러 나면 safe_call이 gr.Error로 바꿈", False)
except gr.Error as e:
	check("9 에러 나면 safe_call이 gr.Error로 바꿈", True, e.message)
admin_view.get_dashboard_data = fake_get_dashboard_data

print(f"\n통과 {passed}/{total}")


# ----------------------------------------------------------------
# 4. 2부: 화면 띄우기 (관리자로 로그인한 것처럼 관리자 탭을 보이게 함)
# ----------------------------------------------------------------
def open_for_test():
	"""테스트용: 관리자 탭 보이게 하기"""
	return gr.update(visible=True)


with gr.Blocks() as screen:
	with gr.Tabs():
		screen_tab = build_admin_tab()

	screen.load(fn=open_for_test, outputs=screen_tab)

screen.launch(theme=gr.themes.Soft())