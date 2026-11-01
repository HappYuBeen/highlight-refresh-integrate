"""회원가입·로그인 · register, login (임세준)

로그인 화면(on_register, on_login)에서 부르는 회원 서비스.
DB에 저장하고 찾는 일은 user_dao가 하고, 여기서는 중복 확인과 비밀번호 비교를 한다.
"""
from datetime import datetime

from hri.dao.user_dao import insert_user, select_user, select_user_auth
from hri.domain.schemas import User
from hri.exception.errors import LoginFailedError, DuplicateUserError


def register(user_id: str, password: str, name: str) -> bool:
	"""
	회원가입 하기

	순서
	1. select_user()로 같은 아이디가 있는지 확인한다
	   있으면 DuplicateUserError를 발생시킨다
	2. 새 회원 정보(User)를 만든다 (가입하는 사람은 항상 일반 user)
	3. insert_user()로 users 테이블에 저장한다 (비밀번호는 users.password에 저장)
	4. 성공하면 True를 돌려준다

	입력: user_id (아이디), password (비밀번호), name (이름)
	출력: bool (성공하면 True)
	에러: DuplicateUserError (이미 사용 중인 아이디)
	"""
	# 1) 같은 아이디가 이미 있는지 확인
	found_user = select_user(user_id)
	if found_user != None:
		raise DuplicateUserError()

	# 2) 새 회원 정보 (role은 항상 "user", 관리자는 DB에 직접 넣음)
	new_user = User(
		user_id=user_id,
		name=name,
		role="user",
		created_at=datetime.now()
	)

	# 3) DB에 저장
	insert_user(new_user, password)

	# 4) 성공
	return True


def login(user_id: str, password: str) -> User:
	"""
	로그인 하기

	순서
	1. select_user_auth()로 아이디에 맞는 회원 정보를 가져온다 (비밀번호 포함)
	2. 아이디가 없으면 LoginFailedError를 발생시킨다
	3. 저장된 비밀번호와 입력한 비밀번호가 다르면 LoginFailedError를 발생시킨다
	4. 맞으면 비밀번호를 뺀 User로 만들어서 돌려준다

	- 아이디 없음 / 비밀번호 틀림을 나누지 않고 같은 에러로 알린다 (보안)

	입력: user_id (아이디), password (비밀번호)
	출력: User (로그인한 회원 정보, 비밀번호 없음)
	에러: LoginFailedError (아이디 없음 또는 비밀번호 불일치)
	"""
	# 1) 아이디로 회원 정보 가져오기 (비밀번호 포함 딕셔너리)
	found_user = select_user_auth(user_id)

	# 2) 아이디가 없으면 로그인 실패
	if found_user == None:
		raise LoginFailedError()

	# 3) 비밀번호가 다르면 로그인 실패
	if found_user["password"] != password:
		raise LoginFailedError()

	# 4) 로그인 성공 → 비밀번호를 뺀 User로 만들어서 돌려줌
	user = User(
		user_id=found_user["user_id"],
		name=found_user["name"],
		role=found_user["role"],
		created_at=found_user["created_at"]
	)

	return user
