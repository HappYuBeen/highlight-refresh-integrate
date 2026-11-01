"""회원 등록·조회 · insert_user, select_user, select_user_auth (임세준)

users 테이블에 회원을 저장하고, 아이디로 회원을 찾아오는 DAO.
회원가입(register)과 로그인(login)에서 부른다.
"""
from typing import Optional

from hri.dao.db import get_connection
from hri.domain.schemas import User


def insert_user(user: User, password: str) -> int:
	"""
	회원 한 명 저장하기

	user의 아이디·이름·역할과 password(비밀번호)를 users 테이블에 INSERT 한다.
	created_at(가입 시간)은 DB가 알아서 넣는다.

	입력: user (User), password (비밀번호)
	출력: int (저장된 줄 수, 성공하면 1)
	"""
	sql = """
		INSERT INTO users (user_id, password, name, role)
		VALUES (%s, %s, %s, %s)
	"""

	values = (
		user.user_id,
		password,
		user.name,
		user.role
	)

	with get_connection() as conn:
		cursor = conn.cursor()
		cursor.execute(sql, values)
		conn.commit()
		count = cursor.rowcount

	return count


def select_user(user_id: str) -> Optional[User]:
	"""
	아이디로 회원 한 명 찾기 (비밀번호 빼고)

	회원가입할 때 아이디 중복 확인에 쓴다.

	입력: user_id (아이디)
	출력: User (찾으면) / None (없으면)
	"""
	sql = """
		SELECT user_id, name, role, created_at
		FROM users
		WHERE user_id = %s
	"""

	# 값이 1개여도 튜플로 넣어야 해서 뒤에 쉼표를 붙임
	values = (user_id,)

	with get_connection() as conn:
		cursor = conn.cursor()
		cursor.execute(sql, values)
		row = cursor.fetchone()

	# 찾는 아이디가 없으면 None
	if row == None:
		return None

	# 찾은 값(딕셔너리)을 User로 바꿔서 돌려줌
	user = User(
		user_id=row["user_id"],
		name=row["name"],
		role=row["role"],
		created_at=row["created_at"]
	)

	return user


def select_user_auth(user_id: str) -> Optional[dict]:
	"""
	로그인용으로 회원 한 명 찾기 (비밀번호 포함)

	로그인할 때 입력한 비밀번호와 비교하려고 password까지 가져온다.
	비밀번호가 들어 있어서 User가 아니라 딕셔너리로 돌려준다.

	입력: user_id (아이디)
	출력: dict (찾으면, 키: user_id·password·name·role·created_at) / None (없으면)
	"""
	sql = """
		SELECT user_id, password, name, role, created_at
		FROM users
		WHERE user_id = %s
	"""

	values = (user_id,)

	with get_connection() as conn:
		cursor = conn.cursor()
		cursor.execute(sql, values)
		row = cursor.fetchone()

	# 없으면 fetchone()이 None을 돌려주니까 그대로 돌려줌
	return row
