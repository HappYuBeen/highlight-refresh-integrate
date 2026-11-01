"""예외 처리·에러 로그 · log_error, safe_call (임세준)

화면 이벤트 함수에서 난 오류를 logs/app.log에 남기고, 사용자에게는 안내 문구만 보여 준다.
- log_error(): 오류 한 건을 파일에 기록하고 화면용 안내 문구를 돌려준다
- safe_call(): 화면 이벤트 함수 위에 @safe_call로 붙여 쓰는 데코레이터
- 오류가 나면 log_error()로 기록한 뒤 gr.Error(안내 문구)로 화면에 알림을 띄운다

사용 예
	from hri.util.logger import safe_call

	@safe_call
	def on_search(keyword):
		...
"""
import inspect
import os
from datetime import datetime
from functools import wraps

import gradio as gr

# 기록할 폴더와 파일 (measure_time과 같은 파일)
LOG_FOLDER = "logs"
LOG_PATH = "logs/app.log"

# 사용자에게 보여 줄 안내 문구 (내부 오류 내용은 화면에 보여 주지 않음)
USER_MESSAGE = "처리 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요."


def log_error(e: Exception, context: str) -> str:
	"""
	오류를 logs/app.log에 기록하고 화면에 보여 줄 안내 문구 돌려주기

	기록 형식
		[2026-10-28 14:05:31] [에러] on_upload: ValueError: PDF가 아닙니다

	순서
	1. logs 폴더가 없으면 만든다
	2. datetime.now().strftime('%Y-%m-%d %H:%M:%S')로 지금 시각을 글자로 만든다
	3. open(LOG_PATH, 'a', encoding='utf-8')로 열어서 시각·위치(context)·오류 종류·내용을
	   한 줄 추가한다 ('a'라서 기존 기록은 지워지지 않음)
	4. 사용자에게 보여 줄 안내 문구(USER_MESSAGE)를 돌려준다

	- 기록하다가 또 오류가 나도(디스크 문제 등) 원래 오류 처리가 막히지 않도록
	  기록 부분은 try/except로 감싸고, 안내 문구는 항상 돌려준다
	- 오류 내용(e)은 파일에만 남기고 화면 문구에는 넣지 않는다 (DB 주소 등 내부 정보 노출 방지)

	입력: e (발생한 오류), context (어디서 났는지, 보통 함수 이름)
	출력: str (화면에 보여 줄 안내 문구)
	"""
	try:
		# 1) logs 폴더가 없으면 만들기
		if LOG_FOLDER not in os.listdir():
			os.mkdir(LOG_FOLDER)

		# 2) 지금 시각
		now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

		# 3) logs/app.log에 한 줄 추가하기 (오류 종류 이름 + 오류 내용)
		error_name = type(e).__name__
		with open(LOG_PATH, "a", encoding="utf-8") as file:
			file.write(f"[{now}] [에러] {context}: {error_name}: {e}\n")
	except Exception:
		pass

	# 4) 화면용 안내 문구
	return USER_MESSAGE


def safe_call(func):
	"""
	화면 이벤트 함수를 오류로부터 보호하는 데코레이터

	사용법
		@safe_call
		def on_compare(report_ids):          # 일반 함수 (return)
			...

		@safe_call
		def on_upload(file, user):           # 제너레이터 함수 (yield로 진행 상황을 여러 번 보냄)
			yield "검증 중..."
			...

	동작
	- 일반 함수: 실행 중 오류가 나면 log_error()로 기록하고 gr.Error(안내 문구)를 발생시킨다
	- 제너레이터 함수(inspect.isgeneratorfunction이 True: on_upload·respond_stream 등):
	  for로 값을 하나씩 받아 그대로 yield하다가, 반복 도중 오류가 나도 똑같이 기록하고 gr.Error를 발생시킨다
	  (일반 wrapper로 감싸면 Gradio가 제너레이터로 인식하지 못해 진행 상황이 안 보이고
	   반복 중 오류도 잡히지 않아서 따로 나눠 처리함)
	- gr.Error는 Gradio가 화면 오른쪽 위에 알림창으로 띄워 주므로
	  핸들러마다 출력 개수가 달라도 상관없이 쓸 수 있다
	- 핸들러가 일부러 raise gr.Error("...")로 보낸 안내는 오류 기록 없이 그대로 통과시킨다
	- 오류가 없으면 원래 함수의 결과를 그대로 돌려준다
	- @wraps(func)로 원래 함수 이름과 입력 정보가 유지되어
	  Gradio가 gr.SelectData 같은 특별한 입력도 그대로 넘겨준다

	입력: func (감쌀 화면 이벤트 함수)
	출력: func (오류 처리가 붙은 함수)
	"""
	# 제너레이터 함수 (yield가 있는 함수)
	if inspect.isgeneratorfunction(func):

		@wraps(func)
		def generator_wrapper(*args, **kwargs):
			try:
				# 원래 함수가 yield하는 값을 하나씩 받아서 그대로 넘겨줌
				for value in func(*args, **kwargs):
					yield value
			except gr.Error:
				# 핸들러가 일부러 보낸 안내는 그대로 화면에 표시
				raise
			except Exception as e:
				message = log_error(e, func.__name__)
				raise gr.Error(message)

		return generator_wrapper

	# 일반 함수 (return하는 함수)
	@wraps(func)
	def wrapper(*args, **kwargs):
		try:
			return func(*args, **kwargs)
		except gr.Error:
			# 핸들러가 일부러 보낸 안내는 그대로 화면에 표시
			raise
		except Exception as e:
			message = log_error(e, func.__name__)
			raise gr.Error(message)

	return wrapper