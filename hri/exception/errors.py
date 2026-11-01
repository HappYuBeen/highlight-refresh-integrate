"""공통 예외 · ChartExtractError(엄정현), ExternalApiError (류현수) / InvalidInputError (서동안) / OutputFormatError (김희진) / LoginFailedError, DuplicateUserError (임세준)"""

# (엄정현) 보고서 파일 올라왔을때 OCR과 vision medel이 구조화 실패한 경우.
class ChartExtractError(Exception):
	"""차트 잘라내기·OCR·Vision 구조화가 실패했을 때 발생하는 예외.

	extract_charts()·ocr_chart()·chart_to_table()에서 raise하고, process_charts 노드가 잡아서
	업로드 전체를 멈추지 않고 그 차트만 건너뛴 뒤 warnings에 str(예외)를 기록한다.
	예) raise ChartExtractError("5쪽 차트: Vision 응답을 ChartData로 바꾸지 못함")
	"""
"""질의응답 예외 (exception.errors)."""

# 류현수
class ExternalApiError(Exception):
	user_message = "데이터를 불러오지 못 했습니다."

	def __init__(self, msg: str):
		super().__init__(msg or self.user_message)
		self.user_message = msg or self.user_message

# 서동안
class InvalidInputError(Exception):
    """빈 질문·공백 입력·최대 길이(1,000자) 초과일 때 validate_question()이 발생시키는 예외."""


# 아이디가 없거나 비밀번호가 다른 경우(임세준)
class LoginFailedError(Exception):
	"""
	로그인 실패 (아이디가 없거나 비밀번호가 다를 때)

	- login()이 아이디가 없거나 비밀번호가 다르면 raise 한다
	- on_login()이 except로 받아서 "아이디 또는 비밀번호가 올바르지 않습니다." 를 보여 준다
	- 보안을 위해 아이디 없음 / 비밀번호 틀림을 나누지 않고 같은 에러로 알린다
	- Exception을 상속해서 raise / except 를 쓸 수 있고, 추가하는 기능은 없다
	"""

#아이디가 중복인 경우(임세준)
class DuplicateUserError(Exception):
	"""
	회원가입 실패 (이미 사용 중인 아이디일 때)

	- register()가 select_user()로 확인해서 같은 아이디가 있으면 raise 한다
	  (users.user_id는 겹칠 수 없음 = UNIQUE 제약)
	- on_register()가 except로 받아서 "이미 사용 중인 아이디입니다." 를 보여 준다
	- Exception을 상속해서 raise / except 를 쓸 수 있고, 추가하는 기능은 없다
	"""

# 김희진
class OutputFormatError(Exception):
	"""LLM 출력이 요구한 형식에 맞지 않아 재시도 후에도 실패했을 때"""
