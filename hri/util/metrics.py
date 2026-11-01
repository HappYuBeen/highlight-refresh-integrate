"""응답 시간 측정 · measure_time (임세준)

함수가 실행되는 데 걸린 시간(응답 시간)을 재서 logs/app.log에 기록하는 데코레이터.
응답 시간을 재고 싶은 함수 위에 @measure_time을 붙여서 쓴다.
"""
import os
import time
from datetime import datetime
from functools import wraps

# 기록할 폴더와 파일
LOG_FOLDER = "logs"
LOG_PATH = "logs/app.log"


def measure_time(func):
	"""
	함수 응답 시간 재기 (데코레이터)

	사용법
		@measure_time
		def summarize_report(report_id):
			...

	순서
	1. 함수를 실행하기 전에 time.time()으로 시작 시간을 잰다
	2. 원래 함수를 그대로 실행하고 결과를 받아 둔다
	3. 끝난 시간과의 차이(응답 시간)를 ms(밀리초)로 바꾼다
	4. logs 폴더가 없으면 만들고, logs/app.log에
	   "[시각] [응답 시간] 함수이름: 걸린 시간 ms" 한 줄을 추가한다
	5. 원래 함수의 결과를 그대로 돌려준다 (결과는 바뀌지 않음)

	- @wraps(func)를 붙여서 감싼 뒤에도 원래 함수 이름과 docstring이 유지된다
	- *args, **kwargs로 원래 함수의 입력을 그대로 넘긴다
	- 질의응답 응답 시간은 이 데코레이터가 아니라 save_log_node의 started_at으로 잰다

	입력: func (응답 시간을 잴 함수)
	출력: 응답 시간을 재는 기능이 붙은 함수
	"""
	@wraps(func)
	def wrapper(*args, **kwargs):
		# 1) 시작 시간
		start = time.time()

		# 2) 원래 함수 실행
		result = func(*args, **kwargs)

		# 3) 응답 시간 (초 → ms, 소수점 첫째 자리까지)
		end = time.time()
		ms = round((end - start) * 1000, 1)

		# 4) logs 폴더가 없으면 만들기
		if LOG_FOLDER not in os.listdir():
			os.mkdir(LOG_FOLDER)

		# logs/app.log에 한 줄 추가하기 ("a": 기존 내용 뒤에 이어 쓰기)
		now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
		with open(LOG_PATH, "a", encoding="utf-8") as file:
			file.write(f"[{now}] [응답 시간] {func.__name__}: {ms} ms\n")

		# 5) 원래 결과 그대로 돌려주기
		return result

	return wrapper