"""보고서 탭 · build_upload_tab, on_upload (김희진) / build_report_list_panel (장유빈)"""
import gradio as gr
import os
import logging
from hri.util.logger import safe_call
from hri.service.report_service import owner_filter, search_reports
from hri.service.document_service import upload_report
# ---------------------------------------------------------------------
# [장유빈] 보고서 목록·검색 화면 
# ---------------------------------------------------------------------
TABLE_HEADERS = ["제목", "발행기관", "발행일", "청크 수"]
NO_PUBLISHER = "확인할 수 없는 발행기관"
NO_RESULT_MESSAGE = "검색 결과가 없습니다"
NEED_SELECT_MESSAGE = "보고서를 선택하세요"
SUMMARY_TAB_ID = "summary"  # app.py의 요약·분석 탭 id (build_summary_tab과 맞출 것)
CHAT_TAB_ID = "chat"  # app.py의 질의응답 탭 id (build_chat_tab과 맞출 것)

# app.py가 표 선택(select)을 연결할 때 쓰는 컴포넌트 모음 (login_components 등과 같은 방식)
report_list_components = {}


def build_report_list_panel(state: gr.State | None = None):
	"""보고서 목록·검색 화면을 만든다. (gr.Blocks 안에서 호출)

	'보고서 라이브러리' 제목 줄에 검색어 입력칸·검색 버튼을 두고, 그 아래에 '보고서 목록' 메뉴(보고서 표)를 펼쳐 둔다.
	보고서 탭을 열거나(app.py) 메뉴를 다시 펼치거나 검색하면 목록을 불러오고, 표에서 한 줄을 누르면 그 보고서를 선택한다.
	선택한 보고서의 report_id는 state['report_ids']에 담겨 요약·질의응답 탭으로 넘어간다.
	state를 받으면 로그인한 사용자 기준으로 목록을 걸러 보여 준다
	(일반 사용자는 본인이 올린 보고서만, 관리자는 전체).

	반환: (화면 Column, '요약 보기' 버튼, '질의응답으로 이동' 버튼)
	표 선택(table.select)과 이동 버튼의 click 연결은 app.py의 build_app()에서 한다
	(검색어·표·report_id 목록·안내 문구는 report_list_components로 꺼내 쓴다).
	"""
	with gr.Column(variant="panel") as panel:
		# 제목은 왼쪽, 검색칸·검색 버튼은 오른쪽 (UI 레퍼런스: '보고서 라이브러리' 머리줄)
		with gr.Row(equal_height=True):
			gr.Markdown("### 보고서 라이브러리")
			keyword = gr.Textbox(
				show_label=False,
				placeholder="제목 또는 발행기관 (비워 두고 검색하면 전체 목록)",
				container=False,
				scale=2,
			)
			search_btn = gr.Button("검색", variant="primary", scale=0, min_width=90)

		# 보고서 목록 (처음부터 펼쳐 둠, 보고서 탭을 열면 app.py가 목록을 불러온다)
		with gr.Accordion("보고서 목록", open=True) as list_menu:
			table = gr.Dataframe(
				headers=TABLE_HEADERS,
				datatype=["str", "str", "str", "number"],
				value=[],
				interactive=False,
				wrap=True,
			)
			message = gr.Markdown()

		with gr.Row():
			go_summary_btn = gr.Button("요약 보기")
			go_chat_btn = gr.Button("질의응답으로 이동", variant="primary")

		# 표의 각 줄이 어떤 report_id인지 기억해 두는 값 (표에는 report_id 열이 없다)
		listed_ids = gr.State([])

	outputs = [table, listed_ids, message]
	# 로그인한 사용자별로 걸러 보여 주려고 state를 같이 넘긴다 (state가 없으면 검색어만)
	inputs = [keyword, state] if state is not None else [keyword]
	# 메뉴를 펼칠 때 목록을 불러온다 (검색어가 비어 있으면 전체 목록)
	list_menu.expand(on_report_search, inputs=inputs, outputs=outputs)
	# 검색하면 결과를 채우고, 접혀 있으면 메뉴를 펼쳐서 보여 준다
	search_btn.click(on_report_search, inputs=inputs, outputs=outputs).then(
		lambda: gr.update(open=True), outputs=list_menu
	)
	keyword.submit(on_report_search, inputs=inputs, outputs=outputs).then(
		lambda: gr.update(open=True), outputs=list_menu
	)
	report_list_components.update(keyword=keyword, table=table, listed_ids=listed_ids, message=message)
	return panel, go_summary_btn, go_chat_btn


def on_report_search(keyword: str, state: dict | None = None):
	"""검색어로 보고서를 찾아 표에 넣을 값을 만든다. (로그인한 사용자 기준)

	일반 사용자는 본인이 올린 보고서만, 관리자는 전체를 찾는다. 로그인 전이면 아무것도 찾지 않는다.
	반환: (표 행 목록, 각 행의 report_id 목록, 안내 문구)
	결과가 없으면 빈 표와 '검색 결과가 없습니다'를 돌려준다.
	"""
	user = (state or {}).get("user")
	reports = search_reports(keyword or "", user_id=owner_filter(user))
	if not reports:
		return [], [], NO_RESULT_MESSAGE
	rows = [
		[
			r.title,
			r.publisher or NO_PUBLISHER,
			r.published_date.isoformat() if r.published_date else "",
			r.chunk_count,
		]
		for r in reports
	]
	ids = [r.report_id for r in reports]
	return rows, ids, f"총 {len(rows)}건"


def on_report_select(evt: gr.SelectData, report_ids: list[int], state: dict):
	"""표에서 누른 줄의 보고서를 선택해 state['report_ids']에 담는다.

	반환: (바뀐 state, 안내 문구)
	"""
	row = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
	if not report_ids or row >= len(report_ids):
		return state, NEED_SELECT_MESSAGE
	new_state = {**state, "report_ids": [report_ids[row]]}
	title = evt.row_value[0] if evt.row_value else ""
	return new_state, f"선택한 보고서: {title}"


def _go_selected(state: dict, tab_id: str):
	"""선택한 보고서가 있으면 tab_id 탭으로 옮기고 보고서 목록(CheckboxGroup)의 선택지와 값을 함께 채워 돌려준다.

	버튼으로 탭을 옮기면 탭의 select 이벤트(refresh_report_choices)가 실행되지 않아서 선택지도 여기서 채운다.
	(선택지에 없는 값을 넣으면 CheckboxGroup이 오류를 낸다)
	선택한 보고서가 없으면 경고만 띄우고 화면은 그대로 둔다.
	"""
	report_ids = (state or {}).get("report_ids") or []
	if not report_ids:
		gr.Warning(NEED_SELECT_MESSAGE)
		return gr.update(), gr.update()
	reports = search_reports("", user_id=owner_filter((state or {}).get("user")))
	choices = [(r.title, r.report_id) for r in reports]
	existing = {r.report_id for r in reports}
	return gr.update(selected=tab_id), gr.update(choices=choices, value=[i for i in report_ids if i in existing])


def on_view_summary(state: dict):
	"""'요약 보기' 버튼: 요약·분석 탭으로 옮기고 선택한 보고서를 고른다.

	반환: (Tabs 갱신, 요약 탭 보고서 드롭다운 갱신)
	"""
	return _go_selected(state, SUMMARY_TAB_ID)


def on_go_chat(state: dict):
	"""'질의응답으로 이동' 버튼: 질의응답 탭으로 옮기고 선택한 보고서를 고른다.

	반환: (Tabs 갱신, 질의응답 탭 보고서 드롭다운 갱신)
	"""
	return _go_selected(state, CHAT_TAB_ID)

# ----------------------------------------
# [김희진] 업로드 화면
# ----------------------------------------
# 업로드 그래프 노드 이름 -> 화면에 보여 줄 단계 이름 (upload_report가 내보내는 step 값)
STEP_LABELS = {
	"validate": "PDF 검증",
	"load_text": "텍스트 추출",
	"extract_tables": "표 추출",
	"extract_charts": "차트 감지",
	"process_charts": "차트 분석(OCR)",
	"split": "청크 분할",
	"summarize_tables": "표, 차트 요약",
	"persist": "저장"
}
NEED_FILE_MESSAGE = "PDF 파일을 선택하세요."
NEED_LOGIN_MESSAGE = "로그인이 필요합니다."

logger = logging.getLogger(__name__)

# app.py가 업로드 버튼 click을 연결할 때 쓰는 컴포넌트 모음 (login_components 등과 같은 방식)
upload_components = {}

def build_upload_tab():
	"""보고서 업로드 화면을 만든다. (gr.Tabs 안에서 호출)
	PDF 선택칸(file_types=['.pdf']), 업로드 버튼, 처리 결과 Markdown을 둔다.
	업로드 버튼 click은 요약, 질의응답 탭의 드롭다운도 갱신해야 해서 build_app()에서 on_upload()에 연결한다.
	(파일, 버튼, 결과는 upload_components로 꺼내 쓴다).
	
	출력: gr.Tab (업로드 탭)
	"""
	with gr.Tab("업로드", id="upload") as tab:
		file = gr.File(label="보고서 PDF", file_types=[".pdf"],file_count="multiple" , type="filepath")
		upload_btn = gr.Button("업로드", variant="primary")
		result = gr.Markdown()

		upload_components.update(file=file, upload_btn=upload_btn, result=result)
		return tab

def _progress_markdown(done_steps: list[str]) -> str:
	"""끝난 단계를 체크 표시로 보여 주는 진행 상황 문구."""
	return "\n".join(f"✅ {STEP_LABELS.get(step, step)}" for step in done_steps)

def _result_markdown(event: dict, report) -> str:
	"""업로드 성공 결과 문구 (보고서 정보 + 청크 수 + 차트 경고)."""
	counts = event.get("counts") or {}
	warnings = event.get("warnings") or []
	lines = ["### ✅ 업로드 완료"]

	if report:
		lines += [
			f"- **보고서명**: {report.title}",
			f"- **발행기관**: {report.publisher or NO_PUBLISHER}",
			f"- **발행일**: {report.published_date.isoformat() if report.published_date else '확인할 수 없음'}",
			f"- **페이지 수**: {report.page_count}"
		]
	lines.append(
		f"- **청크 수**: 텍스트 {counts.get('text', 0)}, 표 {counts.get('table', 0)}, 차트 {counts.get('chart', 0)}"
	)
	if warnings:
		lines.append(f"\n⚠️ **확인이 필요한 항목 {len(warnings)}건**")
		lines += [f"- {w}" for w in warnings]

	return "\n".join(lines)

def _find_report(report_id):
	"""방금 올린 보고서의 정보(제목, 발행기관 등)를 목록에서 찾는다.
	못 찾아도 결과는 보여주려고 None을 돌려준다."""
	try:
		return next((r for r in search_reports() if r.report_id == report_id), None)
	except Exception:
		return None

@safe_call
def on_upload(file_paths: list[str] | None, state: dict):
	"""업로드 버튼: 고른 PDF를 한 개씩 upload_report()가 처리하며 진행 상황을 Markdown에 갱신한다. (제네레이터)
	
	순서
	1. 파일이 없거나 로그인 전이면 안내만 보여 준다
	2. 파일마다 '[번호/전체] 파일명' 블록을 만들고, progress 이벤트마다 끝난 단계를 갱신한다
	3. 한 파일이 실패해도(검증 실패 등) 그 블록에 사유를 적고 다음 파일로 넘어간다.
	4. 모두 끝나면 성공한 파일이 있을 때만 search_reports()로 요약, 질의응답 탭 드롭다운 choices를 갱신한다.
	   (로그인한 사용자 기준: 일반 사용자는 본인 보고서만, 관리자는 전체)
	
	yield 값: (진행/결과 Markdown, 요약 탭 드롭다운 갱신, 질의응답 탭 드롭다운 갱신)
	"""
	keep = gr.update() # "바꾸지 않음"

	# 1. 입력 확인 (파일 1개가 문자열로 와도 목록으로 바꿔 처리)
	if isinstance(file_paths, str):
		file_paths = [file_paths]
	if not file_paths:
		yield NEED_FILE_MESSAGE, keep, keep
		return
	user = (state or {}).get("user")

	if not user:
		yield NEED_LOGIN_MESSAGE, keep, keep
		return
	total = len(file_paths)
	blocks: list[str] = [] # 끝난 파일들의 블록
	success_count = 0

	def screen(cuurent: str = "") -> str:
		return "\n\n---\n\n".join([*blocks, cuurent] if cuurent else blocks)

	# 2. 파일별 처리
	for no, path in enumerate(file_paths, 1):
		header = f"### [{no}/{total}] {os.path.basename(path)}"
		done_steps: list[str] = []
		outcome = None # 이 파일의 마지막 이벤트 (result 또는 error)
		yield screen(f"{header}\n⏳ 시작합니다"), keep, keep

		try:
			for event in upload_report(path, user["user_id"]):
				if event.get("type") == "progress":
					done_steps.append(event["step"])
					yield screen(f"{header}\n{_progress_markdown(done_steps)}"), keep, keep
				else:
					outcome = event # break하지 않고 끝까지 돌려야 upload_report의 마무리(사용량 기록)가 실행된다
		except Exception:
			logger.exception("업로드 화면 처리 실패: %s", path)
			if outcome is None:
				outcome = {"type": "error", "reason": "업로드 처리 중 오류가 발생했습니다."}

		# 3. 이 파일의 결과 블록
		if outcome and outcome.get("type") == "result":
			success_count += 1
			blocks.append(f"{header}\n{_result_markdown(outcome, _find_report(outcome.get('report_id')))}")
		else:
			reason = (outcome or {}).get("reason", "알 수 없는 오류")
			blocks.append(f"{header}\n❌ **업로드 실패**: {reason}")

		yield screen(), keep, keep

	# 4. 성공한 파일이 있으면 드롭다운 갱신 (목록을 못 가져와도 결과는 이미 보여 줬다)
	dropdown = keep
	if success_count:
		try:
			dropdown = gr.update(choices=[(r.title, r.report_id) for r in search_reports(user_id=owner_filter(user))])
		except Exception:
			logger.exception("보고서 목록 갱신 실패")

	summary_line = f"### 업로드 {total}건 중 {success_count}건 완료"
	yield f"{summary_line}\n\n{screen()}", dropdown, dropdown