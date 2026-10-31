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

# 로그인 폼 칸을 가운데에 고정하는 스타일 (rem·em만 씀, 그리드 없음, 색 지정 없음)
LOGIN_BOX_STYLE = """
<style>
#login-wrap {
	display: flex;
	flex-direction: row;
	justify-content: center;
	width: 100%;
}
#login-box {
	box-sizing: border-box;
	position: fixed;
	top: 50%;
	left: 50%;
	transform: translate(-50%, -50%);
	width: 24rem;
	max-width: calc(100% - 2rem);
	padding: 0 1rem;
	text-align: left;
}
#login-title,
#login-title * {
	text-align: center;
}
#login-box * {
	scrollbar-width: none;
}
#login-box *::-webkit-scrollbar {
	display: none;
}
</style>
"""

def build_login_tab():
	"""
	로그인 화면 만들기

	화면 가운데 칸에 아이디·비밀번호(type="password")·이름(회원가입용) 입력칸과
	로그인·회원가입 버튼을 배치한다. (역할·관리자 코드 입력칸은 없음)

	- 처음에는 로그인 화면이고, "회원가입" 버튼을 누르면 같은 탭이 회원가입 화면으로 바뀐다
	  (이름 입력칸과 "가입하기" 버튼이 나타남)
	- "가입하기" 버튼은 여기서 on_register()에 연결한다
	- "로그인" 버튼은 다른 탭들을 보이게 해야 해서 build_app()에서 on_login()에 연결한다
	  그래서 아이디·비밀번호·로그인 버튼·메시지를 login_components에 담아 둔다

	입력: 없음
	출력: gr.Tab (로그인 탭)
	"""
	# 바깥 칸(login-wrap)은 가로 가운데 맞춤, 안쪽 칸(login-box)이 실제 로그인 폼
	with gr.Column(elem_id="login-wrap") as login_tab:
		with gr.Column(elem_id="login-box"):
			# 칸 맨 위에 서비스 이름을 글자로 보여 준다 (회원가입 때는 "## 회원가입"으로 바뀜)
			title = gr.Markdown("# HRI Insight Bridge", elem_id="login-title")

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
				gr.update(),  # 로그인 칸 (바꾸지 않음)
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
				gr.update(),  # 로그인 칸 (바꾸지 않음)
				"# HRI Insight Bridge",  # 제목 (서비스 이름)
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
	5. 로그인 탭은 숨기고, 역할(role)에 맞는 탭 줄만 보이게 한다
	- 일반 사용자("user"): 사용자 탭 줄(보고서·요약·질의응답)을 보이고 보고서 탭으로 이동 (과거 대화 기록은 질의응답 탭 안에 있음)
	- 관리자("admin"): 관리자 탭 줄만 보임
	(관리자는 대시보드만 쓰고, 내부 자료는 DB에 직접 넣는다)

	- 탭 하나하나의 visible을 바꾸면 Gradio(6.30)에서 탭을 눌러도 이동하지 않거나 숨긴 탭이 남아서,
	  탭은 항상 보이게 두고 탭 줄(gr.Tabs) 두 개를 통째로 보이기·숨기기 한다
	- 돌려주는 값 7개는 build_app()의 outputs 순서와 같아야 한다
	  [state, 로그인 탭, 사용자 탭 줄, 관리자 탭 줄, 로그아웃 버튼, 비교할 보고서 목록, 메시지]
	- gr.update()는 "이 부품은 바꾸지 않음"이라는 뜻이다

	입력: user_id (아이디), password (비밀번호)
	출력: tuple (값 7개)
	"""
	# 1) 빈칸이면 메시지만 보여줌 (나머지 6개는 그대로)
	if not user_id or not password:
		return (
			gr.update(),  # state
			gr.update(),  # 로그인 탭
			gr.update(),  # 사용자 탭 줄
			gr.update(),  # 관리자 탭 줄
			gr.update(),  # 로그아웃 버튼
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
			gr.update(),  # 사용자 탭 줄
			gr.update(),  # 관리자 탭 줄
			gr.update(),  # 로그아웃 버튼
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

	# 관리자면 관리자 탭 줄만, 일반 사용자면 사용자 탭 줄만 보여줌
	show_admin = user.role == "admin"

	# 5) 로그인 탭은 숨기고 역할에 맞는 탭 줄만 보여줌
	return (
		state,
		gr.update(visible=False),  # 로그인 칸
		gr.Tabs(selected="report", visible=not show_admin),  # 사용자 탭 줄 (첫 화면은 보고서 탭)
		gr.update(visible=show_admin),  # 관리자 탭 줄
		gr.update(visible=True),  # 로그아웃 버튼
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
	2. 로그인 탭만 보이게 하고 탭 줄 두 개와 로그아웃 버튼은 숨긴다
	3. 비교할 보고서 목록도 비운다

	- 돌려주는 값 6개는 build_app()의 outputs 순서와 같아야 한다
	  [state, 로그인 탭, 사용자 탭 줄, 관리자 탭 줄, 로그아웃 버튼, 비교할 보고서 목록]

	입력: state (gr.State에 들어 있는 딕셔너리)
	출력: tuple (값 6개)
	"""
	# 1) 사용자 정보 지우고 session_id 새로 만들기
	state["user"] = None
	state["session_id"] = str(uuid.uuid4())
	state["report_ids"] = []
	state["last_log_id"] = None

	# 2) 3) 로그인 칸만 보여주고 탭 줄은 숨기기
	return (
		state,
		gr.update(visible=True),  # 로그인 칸
		gr.Tabs(selected="report", visible=False),  # 사용자 탭 줄 숨기기 (다음 로그인 때 보고서 탭부터 보이게 미리 선택)
		gr.update(visible=False),  # 관리자 탭 줄
		gr.update(visible=False),  # 로그아웃 버튼
		gr.update(choices=[], value=[])  # 비교할 보고서 목록 비우기
	)