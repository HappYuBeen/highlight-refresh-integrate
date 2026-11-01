"""관리자 대시보드 · build_admin_tab, on_refresh, get_dashboard_data, get_dashboard_data_v2, plot_daily_counts (임세준)

관리자 계정에만 보이는 대시보드 화면.
- get_dashboard_data(): 대시보드 1차 숫자를 모아 화면 형식(마크다운 표)으로 가공 (3.7.8)
- build_admin_tab(): 사용 현황·답변 품질·성능 비용·문서 관리·운영 상태 화면과 새로고침 버튼 배치 (3.7.9, 2차 화면 3.7.12)
- on_refresh(): 새로고침 버튼을 누르면 get_dashboard_data_v2() 결과로 화면을 갱신 (3.7.9, 2차 화면 3.7.12)
- plot_daily_counts(): 일별 질문 수·평균 응답 시간 선 그래프를 PNG로 저장 (3.7.11)
- get_dashboard_data_v2(): 1차 데이터에 일별 추이 차트·페르소나 비율·처리 방식 비율·피드백 비율·근거 없는 질문 목록을 더해 가공 (3.7.11)
- on_prev_error_page() · on_next_error_page(): 최근 에러 로그 표를 이전·다음 페이지로 넘긴다 (ERROR_PAGE_SIZE개씩)
"""
import math
import re
import uuid

import gradio as gr

from hri.dao.dashboard_dao import (
	select_avg_response_ms,
	select_daily_counts,
	select_feedback_ratio,
	select_no_source_questions,
	select_perf_cost,
	select_persona_ratio,
	select_recent_errors,
	select_route_ratio,
	select_source_ratio,
	select_top_questions
)
from hri.domain.schemas import DashboardData
from hri.llm.llm_client import cost_korean_won, get_usd_krw
from hri.loader.table_loader import table_to_markdown
from hri.service.indicator_service import PLOT_DIR, _use_korean_font
from hri.service.report_service import search_reports
from hri.util.logger import safe_call
from hri.view.report_view import on_upload

# 기록이 없는 표 대신 보여 줄 문구
NO_STATS_MESSAGE = "기록된 통계가 없습니다."

# 최근 에러 로그 한 페이지에 보여 줄 개수
ERROR_PAGE_SIZE = 10

# 원형 그래프 조각 색 (표의 위에서부터 순서대로 씀)
# - 페르소나: 보라 → 파랑 → 하늘색
# - 처리 방식: 초록 → 청록 → 노랑 → 연한 빨강(4번째 오류용)
PERSONA_PIE_COLORS = ["#6366F1", "#3B82F6", "#38BDF8"]
ROUTE_PIE_COLORS = ["#10B981", "#2DD4BF", "#FBBF24", "#F87171"]

# app.py가 관리자 탭의 업로드 버튼 click을 연결할 때 쓰는 컴포넌트 모음 (upload_components와 같은 방식)
admin_components = {}

# 관리자 탭 표 오른쪽에 생기는 작은 스크롤바(위·아래 화살표)를 숨기는 스타일 (스크롤은 그대로 됨)
# - Gradio의 Markdown 칸은 내부에서 overflow: auto로 고정되어 있고 이를 끄는 Python 옵션이 없어서 CSS로만 숨길 수 있음
# - 단, 질문 TOP 10 표(#admin-top-questions)는 표 안 스크롤바를 얇게 보여 준다
# - 표를 전체 화면으로 보면(.fullscreen) 뒤쪽 페이지 스크롤바가 오른쪽에 보여서 그동안만 페이지 스크롤을 막는다
# - app.py에서 head로 다른 스타일과 함께 넣는다
ADMIN_SCROLLBAR_STYLE = """
<style>
#admin-tab * {
	scrollbar-width: none;
}
#admin-tab *::-webkit-scrollbar {
	display: none;
}
/* 질문 TOP 10 표는 행이 많으면 표 안에서 스크롤하도록 스크롤바를 보여 줌 */
#admin-top-questions, #admin-top-questions * {
	scrollbar-width: thin;
}
#admin-top-questions *::-webkit-scrollbar {
	display: initial;
	width: 8px;
}
#admin-top-questions *::-webkit-scrollbar-thumb {
	background: #c1c1c1;
	border-radius: 4px;
}
/* 관리자 탭의 표를 전체 화면으로 볼 때는 뒤쪽 페이지 스크롤바(오른쪽)를 없앰 */
html:has(#admin-tab .fullscreen), body:has(#admin-tab .fullscreen) {
	overflow: hidden;
}
</style>
"""


def _markdown_cells(line: str) -> list[str]:
	"""
	마크다운 표 한 줄을 칸 목록으로 나누기

	'| a | b |' → ['a', 'b']
	- table_to_markdown()이 글자 '|'를 '\\|'로 바꿔 두었으므로, '\\|'는 칸 나눔으로 보지 않고 '|' 글자로 되돌린다

	입력: line (마크다운 표 한 줄)
	출력: list[str] (칸 글 목록)
	"""
	cells = re.split(r"(?<!\\)\|", line.strip())[1:-1]
	return [cell.strip().replace("\\|", "|") for cell in cells]


def _table_frame(lines: list[str]) -> dict:
	"""
	마크다운 표 줄들을 gr.Dataframe 값으로 바꾸기

	첫 줄은 머리글, 둘째 줄(|---|)은 구분선이라 건너뛰고, 나머지는 한 줄에 기록 하나씩 넣는다.

	입력: lines (마크다운 표 줄 목록, 머리글·구분선 포함)
	출력: dict ({"headers": 머리글 칸 목록, "data": 기록 칸 목록의 목록})
	"""
	headers = _markdown_cells(lines[0])
	data = [_markdown_cells(line) for line in lines[2:]]
	return {"headers": headers, "data": data}


def _table_or_message(table: str | None) -> dict:
	"""
	표에 기록이 없으면 안내 문구로 바꾸기

	table_to_markdown()이 만든 표가 머리글·구분선 두 줄뿐이면(기록 없음) '기록된 통계가 없습니다.' 한 칸짜리 표를,
	기록이 있으면 표를 gr.Dataframe 값으로 바꿔 돌려준다.

	입력: table (마크다운 표)
	출력: dict (gr.Dataframe 값 {"headers": ..., "data": ...})
	"""
	if table == None or table.count("\n") <= 1:
		return {"headers": ["안내"], "data": [[NO_STATS_MESSAGE]]}
	return _table_frame(table.split("\n"))


def _ranked_table(table: str | None) -> dict:
	"""
	질문 TOP 10 표 맨 앞에 순위(1, 2, 3, ...) 칸 붙이기

	표는 이미 많이 물어본 순서로 정렬되어 있으므로, 위에서부터 차례대로 순위를 매긴다.
	- 기록이 없으면 '기록된 통계가 없습니다.' 안내 표를 그대로 돌려준다

	입력: table (질문 TOP 10 마크다운 표)
	출력: dict (gr.Dataframe 값 {"headers": ["순위", ...], "data": [[1, ...], ...]})
	"""
	frame = _table_or_message(table)
	if frame["headers"] == ["안내"]:
		return frame

	data = []
	for rank, row in enumerate(frame["data"], start=1):
		data.append([rank] + row)
	return {"headers": ["순위"] + frame["headers"], "data": data}


def _error_page(table: str | None, page: int):
	"""
	최근 에러 로그 표에서 한 페이지만 잘라 보여 주기

	table_to_markdown()이 만든 표는 머리글·구분선 두 줄 다음에 한 줄에 기록 하나씩이라,
	본문 줄을 ERROR_PAGE_SIZE개씩 잘라 머리글을 붙이고, 표 이름(label)에 'n / 전체 페이지'를 적는다.

	- page가 범위를 넘으면 1 또는 마지막 페이지로 맞춘다
	- 기록이 없으면 '기록된 통계가 없습니다.'

	입력: table (최근 에러 로그 전체 마크다운 표), page (보고 싶은 페이지 번호)
	출력: (gr.update 화면에 보여 줄 표·페이지 문구, dict 페이지 상태 {"table": 전체 표, "page": 현재 페이지})
	"""
	if table == None:
		table = ""
	lines = table.split("\n")
	body = lines[2:]

	if len(body) == 0:
		return gr.update(value=_table_or_message(table), label="0 / 0 페이지 (총 0건)"), {"table": table, "page": 1}

	total_pages = math.ceil(len(body) / ERROR_PAGE_SIZE)
	page = min(max(page, 1), total_pages)

	start = (page - 1) * ERROR_PAGE_SIZE
	frame = _table_frame(lines[:2] + body[start:start + ERROR_PAGE_SIZE])
	label = f"{page} / {total_pages} 페이지 (총 {len(body)}건)"
	return gr.update(value=frame, label=label), {"table": table, "page": page}


@safe_call
def on_prev_error_page(page_state):
	"""
	최근 에러 로그 '이전' 버튼 눌렀을 때 (한 페이지 앞으로)

	입력: page_state (gr.State, {"table": 전체 표, "page": 현재 페이지})
	출력: (gr.update 표·페이지 문구, dict 페이지 상태)
	"""
	return _error_page(page_state["table"], page_state["page"] - 1)


@safe_call
def on_next_error_page(page_state):
	"""
	최근 에러 로그 '다음' 버튼 눌렀을 때 (한 페이지 뒤로)

	입력: page_state (gr.State, {"table": 전체 표, "page": 현재 페이지})
	출력: (gr.update 표·페이지 문구, dict 페이지 상태)
	"""
	return _error_page(page_state["table"], page_state["page"] + 1)

# on_upload()가 파일별 결과 블록 사이에 넣는 구분선 (이걸로 나눠 파일 하나를 한 페이지로 보여 줌)
UPLOAD_BLOCK_SEPARATOR = "\n\n---\n\n"


def _reports_frame() -> dict:
	"""
	보고서 목록 표만 새로 만들기 (업로드 중 파일 하나가 끝날 때마다 보고서 목록을 바로 갱신하려고)

	get_dashboard_data()의 5) 보고서 목록과 같은 [제목, 발행일, 청크 수] 표를 gr.Dataframe 값으로 돌려준다.

	입력: 없음
	출력: dict (gr.Dataframe 값)
	"""
	report_rows = [["제목", "발행일", "청크 수"]]
	for report in search_reports():
		report_rows.append([report.title, report.published_date, report.chunk_count])
	return _table_or_message(table_to_markdown(report_rows))


def _upload_page(page_state: dict, page: int):
	"""
	업로드 결과에서 한 페이지(파일 하나)만 보여 주기

	- page가 범위를 넘으면 1 또는 마지막 페이지로 맞춘다
	- 페이지 문구: '업로드 4건 중 1건 완료 · 2 / 4 페이지' (모두 끝나기 전에는 요약 없이 'n / 전체 페이지')

	입력: page_state ({"pages": 페이지별 마크다운 목록, "page": 현재 페이지, "summary": 끝난 뒤 요약 문구}), page (보고 싶은 페이지)
	출력: (str 이 페이지 마크다운, str 페이지 문구, dict 페이지 상태)
	"""
	pages = page_state.get("pages") or []
	summary = page_state.get("summary") or ""
	if len(pages) == 0:
		return "", summary, {"pages": [], "page": 1, "summary": summary}

	page = min(max(page, 1), len(pages))
	info = f"{page} / {len(pages)} 페이지"
	if summary != "":
		info = f"{summary} · {info}"
	return pages[page - 1], info, {"pages": pages, "page": page, "summary": summary}


@safe_call
def on_admin_upload(file_paths, state):
	"""
	관리자 탭 업로드 버튼 눌렀을 때 (on_upload()를 감싸 파일 하나를 한 페이지로 보여 줌, 제너레이터)

	업로드 처리는 김희진 님 on_upload()를 그대로 쓰고, 화면만 바꾼다.
	- on_upload()가 보내는 마크다운을 파일별 블록(구분선 ---)으로 나눠 페이지 목록으로 만든다
	- 업로드하는 동안에는 지금 처리 중인 파일 페이지를 보여 주고, 다음 파일로 넘어가면 다음 페이지로 넘어간다
	- 모두 끝나면 맨 위 요약 줄('### 업로드 n건 중 m건 완료')은 페이지 문구로 옮긴다
	- 파일 하나가 끝나 페이지 수가 늘어날 때마다(다음 파일 시작·전체 끝) 보고서 목록을 바로 갱신한다
	- 파일 없음·로그인 필요 같은 안내 문구는 한 페이지로 보여 준다
	- 끝난 페이지는 표 밑 이전·다음 버튼(on_prev_upload_page·on_next_upload_page)으로 다시 볼 수 있다
	- 이전·다음 버튼 줄은 업로드할 파일이 2개 이상일 때만 보이고, 1개 이하면 숨긴다 (업로드 전에는 숨겨 둠)

	입력: file_paths (고른 PDF 경로 목록), state (gr.State, 로그인 사용자)
	yield 값: (업로드 결과 마크다운, 페이지 문구, 페이지 상태, 이전·다음 버튼 줄 보이기, 보고서 목록 표, 요약 탭 드롭다운, 질의응답 탭 드롭다운)
	"""
	keep = gr.update()
	page_count = 0
	reports = keep

	# 업로드할 파일 수 (파일 1개가 문자열로 와도 1개로 셈) → 2개 이상일 때만 이전·다음 버튼 보이기
	if isinstance(file_paths, str):
		file_count = 1
	elif file_paths:
		file_count = len(file_paths)
	else:
		file_count = 0
	page_buttons = gr.update(visible=file_count >= 2)

	for text, summary_dropdown, chat_dropdown in on_upload(file_paths, state):
		summary = ""
		# 모두 끝난 뒤 마지막 화면은 맨 위에 요약 줄이 붙어 옴
		if text.startswith("### 업로드 ") and "건 중 " in text.split("\n", 1)[0]:
			first_line, _, text = text.partition("\n\n")
			summary = first_line.replace("### ", "")

		pages = text.split(UPLOAD_BLOCK_SEPARATOR)

		# 페이지 수가 늘거나 모두 끝나면(=파일 하나가 끝남) 보고서 목록 갱신
		if (len(pages) > page_count and page_count > 0) or summary != "":
			reports = _reports_frame()
		else:
			reports = keep
		page_count = len(pages)

		page_text, info, page_state = _upload_page({"pages": pages, "summary": summary}, len(pages))
		yield page_text, info, page_state, page_buttons, reports, summary_dropdown, chat_dropdown


@safe_call
def on_prev_upload_page(page_state):
	"""
	업로드 결과 '이전' 버튼 눌렀을 때 (한 파일 앞으로)

	입력: page_state (gr.State, {"pages": ..., "page": 현재 페이지, "summary": ...})
	출력: (str 마크다운, str 페이지 문구, dict 페이지 상태)
	"""
	return _upload_page(page_state, page_state["page"] - 1)


@safe_call
def on_next_upload_page(page_state):
	"""
	업로드 결과 '다음' 버튼 눌렀을 때 (한 파일 뒤로)

	입력: page_state (gr.State, {"pages": ..., "page": 현재 페이지, "summary": ...})
	출력: (str 마크다운, str 페이지 문구, dict 페이지 상태)
	"""
	return _upload_page(page_state, page_state["page"] + 1)


def get_dashboard_data() -> DashboardData:
	"""
	대시보드 1차 데이터 만들기

	질문 TOP 10·자료 유무 응답 비율(route가 rag·no_answer인 것만)·평균 응답 시간·flow별 토큰·원화 비용(llm_usage)·
	보고서 목록·최근 에러 로그를 table_to_markdown()으로 화면 형식에 맞게 가공한다.

	순서
	1. 질문 TOP 10을 [질문, 횟수] 표로 만든다
	2. 자료 유무 응답 비율('자료에 없습니다' 비율)을 가져온다
	3. select_avg_response_ms()로 전체 답변의 평균 응답 시간을 가져온다 (잡담·오류 제외)
	4. select_perf_cost()의 토큰을 flow·모델별로 더하고, cost_korean_won()으로 flow별 원화 비용을 계산해
	   [flow, 입력 토큰, 출력 토큰, 원화 비용] 표와 원화 비용 합계를 만든다
	5. 보고서 목록을 [제목, 발행일, 청크 수] 표로 만든다
	6. 최근 에러 로그를 [시각, 사용자, 질문, 에러] 표로 만든다
	7. DashboardData에 담아 돌려준다

	- 원화 비용은 DB에 저장하지 않고 여기서 계산해 대시보드에만 보여 준다 (환율은 get_usd_krw())
	- 기록이 없으면 표는 머리글만, 숫자는 0.0

	입력: 없음
	출력: DashboardData
	"""
	# 1) 질문 TOP 10
	top_rows = [["질문", "횟수"]]
	for row in select_top_questions():
		top_rows.append([row["question"], row["count"]])

	# 2) 자료 유무 응답 비율
	source_ratio = select_source_ratio()

	# 3) 평균 응답 시간 (전체 답변 기준, 잡담·오류 제외)
	avg_response_ms = select_avg_response_ms()

	# 4) flow별 토큰·원화 비용
	perf_rows = select_perf_cost()

	# flow → 모델 → 토큰 수 (같은 모델이 여러 날짜에 있으면 더함)
	# 예) {"qa": {"gpt-5-mini-2025-08-07": {"input_tokens": 1200, "output_tokens": 300}}}
	usage_by_flow = {}
	for row in perf_rows:
		flow = row["flow"]
		model = row["model"]

		if flow not in usage_by_flow:
			usage_by_flow[flow] = {}
		if model not in usage_by_flow[flow]:
			usage_by_flow[flow][model] = {"input_tokens": 0, "output_tokens": 0}

		usage_by_flow[flow][model]["input_tokens"] = usage_by_flow[flow][model]["input_tokens"] + row["input_tokens"]
		usage_by_flow[flow][model]["output_tokens"] = usage_by_flow[flow][model]["output_tokens"] + row["output_tokens"]

	usd_krw = get_usd_krw()

	usage_rows = [["flow", "입력 토큰", "출력 토큰", "원화 비용"]]
	cost_krw = 0.0
	for flow, usage in usage_by_flow.items():
		# flow 안의 모델 토큰 더하기
		input_tokens = 0
		output_tokens = 0
		for tokens in usage.values():
			input_tokens = input_tokens + tokens["input_tokens"]
			output_tokens = output_tokens + tokens["output_tokens"]

		# 모델마다 단가가 달라서 모델별 토큰을 그대로 넘겨 원화 계산
		cost = cost_korean_won(usage, usd_krw)
		cost_krw = cost_krw + cost

		usage_rows.append([flow, input_tokens, output_tokens, f"{round(cost, 2)}원"])

	cost_krw = round(cost_krw, 4)

	# 5) 보고서 목록
	report_rows = [["제목", "발행일", "청크 수"]]
	for report in search_reports():
		report_rows.append([report.title, report.published_date, report.chunk_count])

	# 6) 최근 에러 로그
	error_rows = [["시각", "사용자", "질문", "에러"]]
	for row in select_recent_errors():
		error_rows.append([row["created_at"], row["user_id"], row["question"], row["error"]])

	# 7) 화면에 보여 줄 형식으로 담기
	return DashboardData(
		top_questions=table_to_markdown(top_rows),
		source_ratio=source_ratio,
		avg_response_ms=avg_response_ms,
		usage_by_flow=table_to_markdown(usage_rows),
		cost_krw=cost_krw,
		reports=table_to_markdown(report_rows),
		recent_errors=table_to_markdown(error_rows)
	)


def _pie_chart(table: str | None, colors: list[str] | None = None):
	"""
	비율 표를 원형 그래프로 그리기 (페르소나별 사용 비율·처리 방식별 비율)

	get_dashboard_data_v2()가 만든 [이름, 질문 수, 비율] 마크다운 표에서 '합계' 줄을 빼고
	이름·질문 수로 원형 그래프를 그려 gr.Plot 값(matplotlib Figure)으로 돌려준다.

	- 가운데가 빈 도넛 모양 (고리 두께 0.35)
	- 고리 위에 비율(%)을 적고, 이름(질문 수)은 아래 범례로 보여 준다 (이름이 길어도 원 크기가 줄지 않게)
	- 기록이 없으면 그래프 대신 가운데에 '기록된 통계가 없습니다.'를 적은 그림
	- pyplot 대신 Figure를 직접 만들어서 그림을 닫지 않아도 쌓이지 않는다

	입력: table (마크다운 표, 머리글·구분선 포함), colors (조각 색 목록, 표 순서대로 씀, None이면 matplotlib 기본 색)
	출력: matplotlib.figure.Figure
	"""
	from matplotlib.figure import Figure

	import matplotlib.pyplot as plt

	# 한글 글꼴 (못 찾으면 기본 글꼴, plot_daily_counts()와 같은 방식)
	font = _use_korean_font()
	if font != None:
		plt.rcParams["font.family"] = font
	plt.rcParams["axes.unicode_minus"] = False

	# 표 본문에서 이름·질문 수 꺼내기 ('합계' 줄은 뺌)
	labels = []
	counts = []
	if table != None:
		for line in table.split("\n")[2:]:
			cells = _markdown_cells(line)
			if len(cells) < 2 or cells[0] == "합계":
				continue
			labels.append(cells[0])
			counts.append(int(cells[1]))

	fig = Figure(figsize=(5, 4.5), layout="constrained")
	ax = fig.add_subplot()

	if len(counts) == 0 or sum(counts) == 0:
		ax.text(0.5, 0.5, NO_STATS_MESSAGE, ha="center", va="center")
		ax.axis("off")
		return fig

	wedges, _, _ = ax.pie(
		counts,
		autopct="%1.1f%%",
		startangle=90,
		counterclock=False,
		pctdistance=0.825,
		colors=colors,
		# width로 고리 두께를 정해 가운데를 비움 (반지름 1 중 바깥 0.35만 칠함, 비율 글자는 고리 한가운데 0.825에)
		wedgeprops={"edgecolor": "white", "linewidth": 2, "width": 0.35}
	)
	ax.axis("equal")

	legend_labels = []
	for label, count in zip(labels, counts):
		legend_labels.append(f"{label} ({count})")
	ax.legend(wedges, legend_labels, loc="upper center", bbox_to_anchor=(0.5, 0), ncol=2, frameon=False)

	return fig


def _admin_table(**options) -> gr.Dataframe:
	"""
	관리자 탭 표(gr.Dataframe) 만들기

	표마다 같은 모양으로 만든다.
	- 보기 전용 (interactive=False), 표 이름은 위 작은 제목(####)이 있어서 숨김 (show_label=False)
	- 긴 글(질문·에러)은 칸 안에서 줄을 바꿔 다 보이게 함 (wrap=True)
	- options로 넘긴 값(표 이름·칸 너비 등)은 위 기본값보다 우선한다

	입력: options (gr.Dataframe에 더 넘길 옵션)
	출력: gr.Dataframe
	"""
	settings = {"interactive": False, "show_label": False, "wrap": True}
	settings.update(options)
	return gr.Dataframe(**settings)


def build_admin_tab():
	"""
	관리자 대시보드 화면 만들기

	사용 현황·답변 품질·성능 비용·문서 관리·운영 상태를
	gr.Dataframe(표)·gr.Image(차트)로 구성하고 새로고침 gr.Button을 둔다.

	구역별 화면 (1차 + 2차)
	- 사용 현황: 질문 TOP 10 표(순위 포함), 일별 질문 수 추이 차트, 페르소나별 사용 비율 원형 그래프, 처리 방식(보고서 근거 답변·자료에 없음·잡담·오류)별 비율 원형 그래프
	- 답변 품질: '자료에 없습니다' 응답 비율, 👍/👎 피드백 비율 표, 근거 없는 질문 목록 표
	- 성능·비용: 평균 응답 시간·원화 비용 합계, flow별 토큰 표
	- 문서 관리: 보고서 목록 표
	- 운영 상태: 최근 에러 로그 표 (ERROR_PAGE_SIZE개씩, 표 밑 이전·다음 버튼으로 페이지 넘김)

	- 관리자 탭 줄(gr.Tabs) 안에 보이게 만들고, 관리자 계정에만 보이도록 build_app()이 탭 줄을 통째로 숨겨 둔다 (관리자가 로그인하면 on_login이 보이게 함)
	- 새로고침 버튼은 state가 필요 없어서 여기서 바로 on_refresh()와 연결한다
	- 새로고침 버튼은 화면 오른쪽 위에 둔다
	- 화면 배치: 머리글(제목·새로고침) 아래에 탭(gr.Tabs) 3개를 두어 한 화면에 한 탭씩 보여 준다
	  1. 보고서 관리(보고서 업로드·보고서 목록): 중요해서 첫 번째 탭
	  2. 통계
	     - 사용 현황: 일별 차트 카드를 위에 넓게, 표·원형 그래프 3개 카드는 그 아래 한 줄에
	     - 답변 품질(왼쪽)·성능 비용(오른쪽) 카드를 그 아래에 나란히
	  3. 시스템 에러 로그(최근 에러 로그)
	  - 새로고침·탭 자동 갱신은 모든 탭의 내용을 한 번에 갱신한다
	- 구역마다 카드(gr.Column(variant="panel"))로 묶고, 표마다 작은 제목(####)을 붙인다 (CSS 없이 Gradio 옵션만 사용, 차트는 높이 480으로 넓게)
	- 표는 gr.Dataframe으로 보여 준다 (옅은 선·머리글 배경은 Dataframe 기본 모양, 모양은 _admin_table()에서 한 번에 정함)
	- 최근 에러 로그 표는 column_widths로 시각·사용자 칸을 좁게 고정해 줄바꿈되지 않게 하고, 에러 칸을 넓힌다
	- 자료 유무 응답 비율·평균 응답 시간·원화 비용은 테두리 칸(gr.Markdown(container=True))으로 강조해 보여 준다
	- 관리자가 로그인하면 새로고침을 누르지 않아도 build_app()이 on_refresh()로 바로 채운다
	- 차트(gr.Image)와 차트 안내 문구는 로그인 전에는 보이지 않는다 (관리자 로그인으로 갱신되면 차트가 있을 때는 차트를, 없을 때는 '일별 질문 수: 표시할 데이터가 없습니다.'를 보여 줌)

	입력: 없음
	출력: gr.Tab (관리자 탭)
	"""
	with gr.Tab("관리자", id="admin", elem_id="admin-tab") as admin_tab:
		# 머리글: 제목·설명은 왼쪽, 새로고침 버튼은 오른쪽 끝 (버튼은 늘어나지 않게 scale=0, 너비 120)
		with gr.Row(equal_height=True):
			gr.Markdown("## 관리자 대시보드")
			refresh_btn = gr.Button("새로고침", variant="primary", scale=0, min_width=120)

		# 구역별 탭 (보고서 관리 · 통계 · 시스템 에러 로그로 나눔, 새로고침 버튼은 탭 위에서 모든 탭을 함께 갱신)
		with gr.Tabs(elem_id="admin-sections"):
			# 보고서 관리 (중요해서 첫 번째 탭: 업로드는 왼쪽, 보고서 목록은 오른쪽)
			with gr.Tab("보고서 관리", id="admin-docs"):
				with gr.Column(variant="panel"):
					gr.Markdown("### 문서 관리 · 보고서 업로드")
					with gr.Row():
						with gr.Column(scale=5, variant="panel"):
							gr.Markdown("#### 보고서 업로드")
							upload_file = gr.File(label="보고서 PDF", file_types=[".pdf"], file_count="multiple", type="filepath")
							upload_btn = gr.Button("업로드", variant="primary")
							# 업로드 결과는 파일 하나씩 페이지로 보여 줌 (길어지면 칸 안에서 스크롤, 높이 400px까지)
							upload_page_info = gr.Markdown()
							upload_result = gr.Markdown(max_height=400)
							# 페이지 넘기기 (결과 밑, 파일 하나씩, 업로드 전에는 숨기고 파일 2개 이상 올릴 때만 보임)
							with gr.Row(visible=False) as upload_page_buttons:
								upload_prev_btn = gr.Button("이전", variant="primary", size="sm")
								upload_next_btn = gr.Button("다음", variant="primary", size="sm")
						with gr.Column(scale=7, variant="panel"):
							gr.Markdown("#### 보고서 목록")
							reports = _admin_table()

			# 통계 (사용 현황 → 답변 품질·성능 비용 순서)
			with gr.Tab("통계", id="admin-stats"):
				# 사용 현황 (차트는 위 카드에 넓게, 표 3개는 그 아래 카드로 한 줄에 나란히)
				gr.Markdown("### 사용 현황")
				with gr.Column(variant="panel"):
					gr.Markdown("#### 일별 질문 수·평균 응답 시간")
					daily_chart = gr.Image(label="일별 질문 수·평균 응답 시간", show_label=False, interactive=False, visible=False, height=480)
					daily_chart_message = gr.Markdown()
				with gr.Row(equal_height=True):
					with gr.Column(variant="panel"):
						gr.Markdown("#### 질문 TOP 10")
						# 긴 질문 때문에 횟수 칸이 밀려 안 보이지 않도록 순위 15%·질문 68%·횟수 17%로 고정
						# 긴 질문으로 표가 길어지면 페이지가 길어지지 않고 표 안에서 스크롤 (높이 400px까지)
						top_questions = _admin_table(column_widths=["15%", "68%", "17%"], max_height=400, elem_id="admin-top-questions")
					# 페르소나·처리 방식 비율은 원형 그래프(gr.Plot)로 보여 줌 (_pie_chart()가 그림)
					with gr.Column(variant="panel"):
						gr.Markdown("#### 페르소나별 사용 비율")
						persona_ratio = gr.Plot(show_label=False)
					with gr.Column(variant="panel"):
						gr.Markdown("#### 처리 방식별 비율")
						route_ratio = gr.Plot(show_label=False)

				# 답변 품질(왼쪽, 넓게)·성능 비용(오른쪽) 카드를 나란히
				with gr.Row(equal_height=True):
					with gr.Column(scale=7, variant="panel"):
						gr.Markdown("### 답변 품질")
						source_ratio = gr.Markdown(container=True, padding=True)
						with gr.Row():
							with gr.Column(scale=5):
								gr.Markdown("#### 👍/👎 피드백 비율")
								feedback_ratio = _admin_table()
							with gr.Column(scale=7):
								gr.Markdown("#### 근거 없는 질문 (보고서 보강 후보)")
								no_source_questions = _admin_table()
					with gr.Column(scale=5, variant="panel"):
						gr.Markdown("### 성능·비용")
						perf_cost = gr.Markdown(container=True, padding=True)
						gr.Markdown("#### flow별 토큰·원화 비용")
						usage_by_flow = _admin_table()

			# 시스템 에러 로그
			with gr.Tab("시스템 에러 로그", id="admin-errors"):
				with gr.Column(variant="panel"):
					gr.Markdown("#### 최근 에러 로그")
					# 칸 너비: 시각·사용자는 좁게 고정, 질문 30%·에러 45% (Gradio는 px·%만 알아들음, 페이지 문구는 표 이름에 나옴)
					# 한 줄 보기: 긴 질문·에러는 줄바꿈하지 않고 칸 밖으로 넘치는 글을 숨겨(wrap=False), 한 페이지 10건이 스크롤 없이 한 번에 보이게 함
					recent_errors = _admin_table(show_label=True, label="1 / 1 페이지", column_widths=["180px", "110px", "30%", "45%"], wrap=False)
					# 페이지 넘기기 (표 밑, ERROR_PAGE_SIZE개씩)
					with gr.Row():
						error_prev_btn = gr.Button("이전", variant="primary", size="sm")
						error_next_btn = gr.Button("다음", variant="primary", size="sm")

	# 최근 에러 로그에서 지금 보고 있는 페이지 (전체 표·현재 페이지 번호)
	error_page_state = gr.State({"table": "", "page": 1})

	# on_refresh()가 돌려주는 순서와 같게, 화면 위에서 아래 순서
	outputs = [
		top_questions, daily_chart, daily_chart_message, persona_ratio, route_ratio,
		source_ratio, feedback_ratio, no_source_questions,
		perf_cost, usage_by_flow,
		reports,
		recent_errors, error_page_state
	]

	# 최근 에러 로그 이전·다음 페이지
	error_prev_btn.click(fn=on_prev_error_page, inputs=[error_page_state], outputs=[recent_errors, error_page_state])
	error_next_btn.click(fn=on_next_error_page, inputs=[error_page_state], outputs=[recent_errors, error_page_state])

	# 업로드 결과에서 지금 보고 있는 페이지 (파일별 결과 목록·현재 페이지·끝난 뒤 요약)
	upload_page_state = gr.State({"pages": [], "page": 1, "summary": ""})

	# 업로드 결과 이전·다음 페이지
	upload_outputs = [upload_result, upload_page_info, upload_page_state]
	upload_prev_btn.click(fn=on_prev_upload_page, inputs=[upload_page_state], outputs=upload_outputs)
	upload_next_btn.click(fn=on_next_upload_page, inputs=[upload_page_state], outputs=upload_outputs)

	# 새로고침 버튼 누르면 대시보드 갱신
	refresh_btn.click(fn=on_refresh, outputs=outputs)

	# 업로드 버튼은 state·다른 탭 드롭다운이 필요해서 app.py에서 on_admin_upload()와 연결한다 (결과는 파일별 페이지로)
	admin_components.update(
		file=upload_file, upload_btn=upload_btn, result=upload_result, outputs=outputs,
		page_info=upload_page_info, page_state=upload_page_state, page_buttons=upload_page_buttons, reports=reports
	)

	return admin_tab


@safe_call
def on_refresh():
	"""
	새로고침 버튼 눌렀을 때

	get_dashboard_data_v2() 결과(1차 + 2차 데이터)로 대시보드 컴포넌트를 갱신한다.

	- 표(질문 TOP 10·피드백 비율·근거 없는 질문·flow별 토큰·보고서 목록·최근 에러)는 마크다운 표를 gr.Dataframe 값으로 바꿔 보여 준다
	- 질문 TOP 10은 _ranked_table()로 맨 앞에 순위 칸을 붙여 보여 준다
	- 페르소나 비율·처리 방식 비율은 _pie_chart()로 원형 그래프(gr.Plot 값)를 그려 보여 준다
	- 자료 유무 응답 비율은 %로, 평균 응답 시간은 초(s)로, 원화 비용 합계는 원으로 보여 준다
	- 일별 질문 수 차트
	  - 차트가 있으면: 이미지 칸을 보이게 하고 차트를 넣고, 안내 문구는 지운다
	  - 차트가 없으면(기록이 없음): 이미지 칸을 숨기고 '일별 질문 수: 표시할 데이터가 없습니다.'를 보여 준다
	- 최근 에러 로그는 첫 페이지(ERROR_PAGE_SIZE개)만 보여 주고, 전체 표는 페이지 상태에 담아 이전·다음 버튼이 쓴다
	  - 돌려주는 값 13개는 build_admin_tab()의 outputs 순서와 같아야 한다
	  [질문 TOP 10, 일별 차트, 차트 안내 문구, 페르소나 비율, 처리 방식 비율,
	   자료 유무 비율, 피드백 비율, 근거 없는 질문,
	   응답 시간·원화 비용, flow별 토큰, 보고서 목록, 최근 에러, 최근 에러 페이지 상태]

	입력: 없음
	출력: tuple (값 13개)
	"""
	data = get_dashboard_data_v2()

	# 숫자는 단위를 붙여서 보여줌
	source_ratio = f"'자료에 없습니다' 응답 비율: **{round(data.source_ratio * 100, 1)}%**"
	# 평균 응답 시간은 ms로 저장되어 있어 초(s)로 바꿔 보여 줌 (1 s = 1000 ms, 소수 둘째 자리까지)
	avg_response_s = round(data.avg_response_ms / 1000, 2)
	perf_cost = f"평균 응답 시간: **{avg_response_s} s** / 원화 비용 합계: **{round(data.cost_krw, 2)}원**"

	# 일별 질문 수 차트 (없으면 안내 문구)
	if data.daily_chart != None:
		daily_chart = gr.update(value=data.daily_chart, visible=True)
		daily_chart_message = ""
	else:
		daily_chart = gr.update(value=None, visible=False)
		daily_chart_message = "일별 질문 수: 표시할 데이터가 없습니다."

	return (
		_ranked_table(data.top_questions),
		daily_chart,
		daily_chart_message,
		_pie_chart(data.persona_ratio, PERSONA_PIE_COLORS),
		_pie_chart(data.route_ratio, ROUTE_PIE_COLORS),
		source_ratio,
		_table_or_message(data.feedback_ratio),
		_table_or_message(data.no_source_questions),
		perf_cost,
		_table_or_message(data.usage_by_flow),
		_table_or_message(data.reports),
		*_error_page(data.recent_errors, 1)
	)


def plot_daily_counts(rows: list[dict]) -> str:
	"""
	일별 추이 차트 그리기

	matplotlib 막대 그래프로 일별 질문 수와 평균 응답 시간을 위아래 두 그래프로 나눠 그려 PNG 한 장으로 저장한다.

	순서
	1. rows를 날짜(가로축)·질문 수·평균 응답 시간 리스트로 나눈다
	   (평균 응답 시간이 None인 날은 float("nan")으로 바꿔 그 날만 막대를 그리지 않는다)
	2. 한글 글꼴을 정한다 (장유빈 님 _use_korean_font() 재사용, 못 찾으면 기본 글꼴)
	3. 위 그래프에 질문 수(건), 아래 그래프에 평균 응답 시간(s)을 막대로 그린다
	   (평균 응답 시간은 ms로 들어오므로 1000으로 나눠 초로 바꿔 그림)
	   (두 값은 단위가 달라서 그래프를 나눔, 가로축 날짜는 두 그래프가 함께 씀)
	   (막대 위에 값을 적고, 값이 없는 날은 적지 않음)
		(질문 수는 건수라서 세로축 눈금을 정수로만 표시)
	4. data/plots/에 daily_uuid.png로 저장하고 그림을 닫는다 (지표 비교 차트와 같은 폴더)

	- 화면 없는 서버에서도 그릴 수 있게 matplotlib.use("Agg")를 쓴다
	- 기록이 없을 때는 부르지 않는다 (get_dashboard_data_v2()가 rows가 있을 때만 부름)

	입력: rows (select_daily_counts() 결과, 예: [{'day': date(2026, 10, 27), 'question_count': 2, 'avg_response_ms': 1500.0}, ...])
	출력: str (저장한 PNG 파일 경로)
	"""
	import matplotlib

	matplotlib.use("Agg")
	import matplotlib.pyplot as plt
	from matplotlib.ticker import MaxNLocator

	# 1) 가로축(날짜)·세로축(질문 수, 평균 응답 시간) 값 나누기
	days = []
	counts = []
	avg_s = []
	for row in rows:
		days.append(str(row["day"]))
		counts.append(row["question_count"])
		if row["avg_response_ms"] == None:
			avg_s.append(float("nan"))
		else:
			# ms → 초(s)
			avg_s.append(row["avg_response_ms"] / 1000)

	# 2) 한글 글꼴 (못 찾으면 기본 글꼴)
	font = _use_korean_font()
	if font != None:
		plt.rcParams["font.family"] = font
	plt.rcParams["axes.unicode_minus"] = False

	# 3) 막대 그래프 그리기 (위: 질문 수, 아래: 평균 응답 시간, 가로축 날짜는 함께 씀)
	fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6), sharex=True, layout="constrained")

	bars1 = ax1.bar(days, counts, color="C0", width=0.25)
	ax1.bar_label(bars1, padding=2)
	ax1.margins(y=0.15)
	# 날짜가 적으면 막대가 가로축을 꽉 채워 굵어 보여서, 양옆에 여백을 둬 얇게 보이게 함 (아래 그래프도 같은 가로축)
	ax1.set_xlim(-1, len(days))
	ax1.set_title("일별 질문 수")
	ax1.set_ylabel("질문 수 (건)")
	ax1.yaxis.set_major_locator(MaxNLocator(integer=True))

	bars2 = ax2.bar(days, avg_s, color="C1", width=0.25)
	s_labels = []
	for sec in avg_s:
		if sec != sec:
			s_labels.append("")
		else:
			s_labels.append(f"{round(sec, 2)}")
	ax2.bar_label(bars2, labels=s_labels, padding=2)
	ax2.margins(y=0.15)
	ax2.set_title("일별 평균 응답 시간")
	ax2.set_ylabel("평균 응답 시간 (s)")
	ax2.set_xlabel("날짜")
	ax2.tick_params(axis="x", rotation=45)

	# 4) PNG로 저장하고 그림 닫기
	PLOT_DIR.mkdir(parents=True, exist_ok=True)
	path = PLOT_DIR / f"daily_{uuid.uuid4().hex}.png"
	fig.savefig(path, dpi=150)
	plt.close(fig)

	return str(path)


def get_dashboard_data_v2() -> DashboardData:
	"""
	대시보드 2차 데이터 만들기

	일별 질문 수, 페르소나별 사용 비율, 피드백 비율, 근거 없는 질문 목록을 대시보드 화면 형식에 맞게 가공한다.
	(잡담[chat]·오류[error]는 qa_logs.route로 답변 품질 통계에서 제외)

	순서
	1. get_dashboard_data()로 1차 데이터를 만든다 (DashboardData 하나에 1차·2차를 함께 담으려고)
	2. 일별 질문 수: 기록이 있으면 plot_daily_counts()로 추이 차트를 그려 경로를 담는다 (없으면 None)
	3. 페르소나별 사용 비율: [페르소나, 질문 수, 비율] 표 (맨 아래 ROLLUP 합계 줄은 '합계')
	   처리 방식별 비율: [처리 방식, 질문 수, 비율] 표 (route를 한글 이름으로 바꿈, 맨 아래 합계 줄은 '합계')
	4. 피드백 비율: [구분, 건수, 비율] 표 (👍·👎·합계)
	5. 근거 없는 질문 목록: [질문, '자료에 없습니다' 횟수] 표

	- 잡담·오류 제외: 답변 품질 통계(피드백 비율·근거 없는 질문)는 SQL에서 이미 rag·no_answer만 센다
	  페르소나별 사용 비율·처리 방식별 비율은 사용 현황이라 모든 질문(잡담·오류 포함)을 센다
	- 비율은 %로, 소수 첫째 자리까지 보여 준다
	- 기록이 없으면 표는 머리글만, 차트는 None

	입력: 없음
	출력: DashboardData (1차 칸 + 2차 칸)
	"""
	# 1) 1차 데이터
	data = get_dashboard_data()

	# 2) 일별 질문 수 → 추이 차트 (기록이 있을 때만)
	daily_rows = select_daily_counts()
	if len(daily_rows) > 0:
		data.daily_chart = plot_daily_counts(daily_rows)

	# 3) 페르소나별 사용 비율 (맨 아래 줄이 합계)
	persona_rows = select_persona_ratio()

	total = 0
	for row in persona_rows:
		if row["persona"] == None:
			total = row["count"]

	persona_table = [["페르소나", "질문 수", "비율"]]
	for row in persona_rows:
		if row["persona"] == None:
			persona_table.append(["합계", row["count"], ""])
		else:
			ratio = round(row["count"] / total * 100, 1)
			persona_table.append([row["persona"], row["count"], f"{ratio}%"])
	data.persona_ratio = table_to_markdown(persona_table)
	
	# 3-2) 처리 방식(route)별 비율 (맨 아래 줄이 합계)
	route_names = {
		"rag": "보고서 근거 답변 (rag)",
		"no_answer": "자료에 없음 (no_answer)",
		"chat": "잡담 (chat)",
		"error": "오류 (error)"
	}
	route_rows = select_route_ratio()

	route_total = 0
	for row in route_rows:
		if row["route"] == None:
			route_total = row["count"]

	route_table = [["처리 방식", "질문 수", "비율"]]
	for row in route_rows:
		if row["route"] == None:
			route_table.append(["합계", row["count"], ""])
		else:
			ratio = round(row["count"] / route_total * 100, 1)
			route_table.append([route_names.get(row["route"], row["route"]), row["count"], f"{ratio}%"])
	data.route_ratio = table_to_markdown(route_table)

	# 4) 피드백 비율 (피드백이 있을 때만 줄 추가)
	feedback = select_feedback_ratio()

	feedback_table = [["구분", "건수", "비율"]]
	if feedback["total"] > 0:
		feedback_table.append(["👍", feedback["up_count"], f"{round(feedback['up_ratio'] * 100, 1)}%"])
		feedback_table.append(["👎", feedback["down_count"], f"{round(feedback['down_ratio'] * 100, 1)}%"])
		feedback_table.append(["합계", feedback["total"], ""])
	data.feedback_ratio = table_to_markdown(feedback_table)

	# 5) 근거 없는 질문 목록
	no_source_table = [["질문", "'자료에 없습니다' 횟수"]]
	for row in select_no_source_questions():
		no_source_table.append([row["question"], row["count"]])
	data.no_source_questions = table_to_markdown(no_source_table)

	return data