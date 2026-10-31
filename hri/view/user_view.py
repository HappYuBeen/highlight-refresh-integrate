"""로그인 화면 · build_login_tab, on_login, on_register, on_logout (임세준)

로그인·회원가입 화면을 만들고, 버튼을 눌렀을 때 할 일을 정하는 파일.
- 회원가입·로그인 판단은 user_service(register, login)가 한다
- 로그인 버튼·로그아웃 버튼 연결은 app.py의 build_app()에서 한다
- 그래서 build_app()에서 쓸 화면 부품을 login_components에 담아 둔다
"""
import uuid
import gradio as gr

from hri.service.user_service import login, register
from hri.service.report_service import owner_filter, search_reports
from hri.exception.errors import LoginFailedError, DuplicateUserError


# build_app()에서 로그인 버튼을 연결할 때 쓰려고 화면 부품을 저장해 둠
login_components = {}

# 로그인 화면 맨 위에 보여 줄 서비스 소개 글 (Markdown)
INTRO = """# HRI Insight Bridge
경제분석보고서(PDF)를 올리면 **텍스트·표·차트(OCR)** 까지 읽어 요약하고, 보고서에 근거해 질문에 답하는 서비스입니다.

> AI 답변은 원문과 다를 수 있어요

- **내부 자료**: 관리자가 올린 보고서(PDF)를 텍스트·표·차트까지 추출해 모든 사용자의 근거로 사용
- **질의응답**: 내부 자료 + **이 대화에 첨부한 내 자료**를 함께 검색해 답변(출처에 [내부 자료]/[내 자료] 표시)
  - 첨부 자료는 그 대화에서만 쓰이며, 대화를 삭제하면 함께 삭제됩니다
	(선택한 보고서의 수치를 ECOS 통계로 검증하는 🔍 버튼 포함)
- **요약·분석**: 3줄 요약, 섹션별 요약, 여러 보고서 병합
- **내 기록**: 이전 대화 이어가기·삭제 · **관리자**: 문서 관리(내부 자료)·사용 현황·비용 대시보드

**예시 질문**
- 2026년 하반기 경제성장률 전망은?
- 소비자물가 상승률이 둔화된 이유는?
- 두 보고서의 성장률 전망을 비교해 줘
"""


def build_login_tab():
	"""
	로그인 화면 만들기

	맨 위에 서비스 소개 글(INTRO)을 보여 주고, 그 아래에
	아이디·비밀번호(type="password")·이름(회원가입용) 입력칸과
	로그인·회원가입 버튼을 배치한다. (역할·관리자 코드 입력칸은 없음)

	- 처음에는 로그인 화면이고, "회원가입" 버튼을 누르면 같은 탭이 회원가입 화면으로 바뀐다
	  (이름 입력칸과 "가입하기" 버튼이 나타남)
	- "가입하기" 버튼은 여기서 on_register()에 연결한다
	- "로그인" 버튼은 다른 탭들을 보이게 해야 해서 build_app()에서 on_login()에 연결한다
	  그래서 아이디·비밀번호·로그인 버튼·메시지를 login_components에 담아 둔다

	입력: 없음
	출력: gr.Tab (로그인 탭)
	"""
	with gr.Tab("로그인", id="login") as login_tab:
		# 서비스 소개 글 (맨 위)
		gr.Markdown(INTRO)

		title = gr.Markdown("## 로그인")

		user_id = gr.Textbox(label="아이디", placeholder="아이디를 입력하세요")
		password = gr.Textbox(label="비밀번호", type="password", placeholder="비밀번호를 입력하세요")
		# 이름은 회원가입할 때만 보여줌
		name = gr.Textbox(label="이름", placeholder="이름을 입력하세요", visible=False)

		# 로그인 화면 버튼
		with gr.Row():
			login_btn = gr.Button("로그인", variant="primary")
			go_register_btn = gr.Button("회원가입")

		# 회원가입 화면 버튼 (처음에는 숨김)
		with gr.Row():
			register_btn = gr.Button("가입하기", variant="primary", visible=False)
			go_login_btn = gr.Button("로그인 화면으로", visible=False)

		message = gr.Markdown()

		def show_register_form():
			"""회원가입 화면으로 바꾸기 (이름 칸·가입하기 버튼 보이기)"""
			return (
				gr.update(label="회원가입"),  # 탭 이름
				"## 회원가입",  # 제목
				gr.update(visible=True),  # 이름 입력칸
				gr.update(visible=False),  # 로그인 버튼
				gr.update(visible=False),  # 회원가입 버튼
				gr.update(visible=True),  # 가입하기 버튼
				gr.update(visible=True),  # 로그인 화면으로 버튼
				""  # 메시지 지우기
			)

		def show_login_form():
			"""로그인 화면으로 바꾸기 (이름 칸·가입하기 버튼 숨기기)"""
			return (
				gr.update(label="로그인"),  # 탭 이름
				"## 로그인",  # 제목
				gr.update(visible=False),  # 이름 입력칸
				gr.update(visible=True),  # 로그인 버튼
				gr.update(visible=True),  # 회원가입 버튼
				gr.update(visible=False),  # 가입하기 버튼
				gr.update(visible=False),  # 로그인 화면으로 버튼
				""  # 메시지 지우기
			)

		# 회원가입 버튼 누르면 회원가입 화면으로
		go_register_btn.click(
			fn=show_register_form,
			outputs=[login_tab, title, name, login_btn, go_register_btn, register_btn, go_login_btn, message]
		)

		# 로그인 화면으로 버튼 누르면 로그인 화면으로
		go_login_btn.click(
			fn=show_login_form,
			outputs=[login_tab, title, name, login_btn, go_register_btn, register_btn, go_login_btn, message]
		)

		# 가입하기 버튼 누르면 회원가입
		register_btn.click(
			fn=on_register,
			inputs=[user_id, password, name],
			outputs=message
		)

	# build_app()에서 쓸 부품 저장
	login_components["user_id"] = user_id
	login_components["password"] = password
	login_components["login_btn"] = login_btn
	login_components["message"] = message

	return login_tab


def on_login(user_id: str, password: str):
	"""
	로그인 버튼 눌렀을 때

	순서
	1. 아이디나 비밀번호가 비어 있으면 화면은 그대로 두고 안내 메시지만 보여 준다
	2. login()으로 로그인한다
	   실패(LoginFailedError)하면 화면은 그대로 두고 안내 메시지만 보여 준다
	3. 성공하면 사용자 정보를 gr.State에 저장할 state로 만든다
	4. 지표 비교 탭에서 고를 보고서 목록을 search_reports()로 만든다 ((제목, report_id) 쌍)
	   (일반 사용자는 본인이 올린 보고서만, 관리자는 전체)
	5. 로그인 탭은 숨기고, 역할(role)에 맞는 탭만 보이게 한다
	- 일반 사용자("user"): 보고서·요약·질의응답·내 기록 탭을 보이고 질의응답 탭으로 이동
	- 관리자("admin"): 관리자 탭만 보이고 관리자 탭으로 이동
	(관리자는 대시보드만 쓰고, 내부 자료는 DB에 직접 넣는다)

	- 돌려주는 값 11개는 build_app()의 outputs 순서와 같아야 한다
	  [state, 로그인 탭, 보고서 탭, 요약 탭, 질의응답 탭, 내 기록 탭, 관리자 탭,
	   로그아웃 버튼, 탭 선택, 비교할 보고서 목록, 메시지]
	- gr.update()는 "이 부품은 바꾸지 않음"이라는 뜻이다
	- 처음부터 보이던 로그인 탭은 visible=False로는 안 숨겨져서 visible="hidden"을 쓴다

	입력: user_id (아이디), password (비밀번호)
	출력: tuple (값 11개)
	"""
	# 1) 빈칸이면 메시지만 보여줌 (나머지 10개는 그대로)
	if not user_id or not password:
		return (
			gr.update(),  # state
			gr.update(),  # 로그인 탭
			gr.update(),  # 보고서 탭
			gr.update(),  # 요약 탭
			gr.update(),  # 질의응답 탭
			gr.update(),  # 내 기록 탭
			gr.update(),  # 관리자 탭
			gr.update(),  # 로그아웃 버튼
			gr.update(),  # 탭 선택
			gr.update(),  # 비교할 보고서 목록
			"아이디와 비밀번호를 입력하세요."
		)

	# 2) 로그인 (실패하면 메시지만 보여줌)
	try:
		user = login(user_id, password)
	except LoginFailedError:
		return (
			gr.update(),  # state
			gr.update(),  # 로그인 탭
			gr.update(),  # 보고서 탭
			gr.update(),  # 요약 탭
			gr.update(),  # 질의응답 탭
			gr.update(),  # 내 기록 탭
			gr.update(),  # 관리자 탭
			gr.update(),  # 로그아웃 버튼
			gr.update(),  # 탭 선택
			gr.update(),  # 비교할 보고서 목록
			"아이디 또는 비밀번호가 올바르지 않습니다."
		)

	# 3) state에 저장할 값 (로그인한 사람 정보 + 새 session_id)
	user_info = {
		"user_id": user.user_id,
		"name": user.name,
		"role": user.role,
		"created_at": user.created_at
	}

	state = {
		"user": user_info,
		"session_id": str(uuid.uuid4()),
		"report_ids": [],
		"last_log_id": None
	}

	# 4) 지표 비교 탭에서 고를 보고서 목록 (보고서를 못 가져와도 로그인은 되게 함)
	choices = []
	try:
		reports = search_reports(user_id=owner_filter(user))
		for report in reports:
			choices.append((report.title, report.report_id))
	except Exception:
		choices = []

	# 관리자면 관리자 탭만, 일반 사용자면 사용자 탭들만 보여줌
	if user.role == "admin":
		show_admin = True
		show_user_tabs = False
		first_tab = "admin"
	else:
		show_admin = False
		show_user_tabs = True
		first_tab = "chat"

	# 5) 로그인 탭은 숨기고 역할에 맞는 탭만 보여줌
	return (
		state,
		gr.update(visible="hidden"),  # 로그인 탭
		gr.update(visible=show_user_tabs),  # 보고서 탭
		gr.update(visible=show_user_tabs),  # 요약 탭
		gr.update(visible=show_user_tabs),  # 질의응답 탭
		gr.update(visible=show_user_tabs),  # 내 기록 탭
		gr.update(visible=show_admin),  # 관리자 탭
		gr.update(visible=True),  # 로그아웃 버튼
		gr.Tabs(selected=first_tab),  # 첫 화면 탭으로 이동 (관리자: 관리자 탭, 일반 사용자: 질의응답 탭)
		gr.update(choices=choices, value=[]),  # 비교할 보고서 목록
		""  # 메시지 지우기
	)


def on_register(user_id: str, password: str, name: str):
	"""
	가입하기 버튼 눌렀을 때

	순서
	1. 아이디·비밀번호·이름 중 빈칸이 있으면 안내 메시지를 돌려준다
	2. register()로 회원가입한다 (가입하는 사람은 항상 일반 user)
	3. 결과에 따라 안내 메시지를 돌려준다
	   - 성공(True): 회원가입 완료
	   - 아이디 중복(DuplicateUserError): 이미 사용 중인 아이디
	   - 그 밖의 에러(DB 연결 실패 등): 회원가입 실패

	입력: user_id (아이디), password (비밀번호), name (이름)
	출력: str (화면에 보여 줄 메시지)
	"""
	# 1) 빈칸 확인
	if not user_id or not password or not name:
		return "아이디, 비밀번호, 이름을 모두 입력하세요."

	# 2) 회원가입
	try:
		register(user_id, password, name)
	except DuplicateUserError:
		return "이미 사용 중인 아이디입니다."
	except Exception as e:
		print("회원가입 에러:", type(e).__name__, e)
		return "회원가입에 실패했습니다. 다시 시도해주세요."

	# 3) 성공
	return "회원가입이 완료되었습니다. 로그인해 주세요."


def on_logout(state):
	"""
	로그아웃 버튼 눌렀을 때

	순서
	1. state의 사용자 정보를 지우고 session_id를 uuid4로 새로 만든다
	   (선택했던 보고서와 마지막 기록 번호도 비운다)
	2. 로그인 탭만 보이게 하고 나머지 탭과 로그아웃 버튼은 숨긴다
	3. 로그인 탭으로 이동하고, 비교할 보고서 목록도 비운다

	- 돌려주는 값 10개는 build_app()의 outputs 순서와 같아야 한다
	  [state, 로그인 탭, 보고서 탭, 요약 탭, 질의응답 탭, 내 기록 탭, 관리자 탭,
	   로그아웃 버튼, 탭 선택, 비교할 보고서 목록]

	입력: state (gr.State에 들어 있는 딕셔너리)
	출력: tuple (값 10개)
	"""
	# 1) 사용자 정보 지우고 session_id 새로 만들기
	state["user"] = None
	state["session_id"] = str(uuid.uuid4())
	state["report_ids"] = []
	state["last_log_id"] = None

	# 2) 3) 로그인 탭만 보여주고 로그인 탭으로 이동
	return (
		state,
		gr.update(visible=True),  # 로그인 탭
		gr.update(visible=False),  # 보고서 탭
		gr.update(visible=False),  # 요약 탭
		gr.update(visible=False),  # 질의응답 탭
		gr.update(visible=False),  # 내 기록 탭
		gr.update(visible=False),  # 관리자 탭
		gr.update(visible=False),  # 로그아웃 버튼
		gr.Tabs(selected="login"),  # 로그인 탭으로 이동
		gr.update(choices=[], value=[])  # 비교할 보고서 목록 비우기
	)