"""질문 기록 저장 + 세션 줄 만들기 테스트 (임세준) - 커밋하지 않는 파일

MySQL 대신 파이썬 기본 SQLite(메모리 DB)에 users·chat_sessions·qa_logs를 만들고
FK(qa_logs.session_id → chat_sessions)를 schema.sql처럼 켜 둔 뒤, 진짜 insert_qa_log()를 실행해 본다.
(DB 설치·.env 필요 없음)

실행 (레포 맨 위 폴더에서):
	poetry run python -m tests.try_qa_log_session

※ SQLite와 MySQL의 차이 (이 테스트에서만 FakeCursor가 바꿔서 실행)
	- 값 자리: MySQL %s → SQLite ?
	- '이미 있으면 그대로 두기': MySQL ON DUPLICATE KEY UPDATE → SQLite ON CONFLICT DO NOTHING
	- FK 검사: SQLite는 PRAGMA foreign_keys = ON을 해야 켜진다
"""
import sqlite3

from hri.dao import qa_log_dao
from hri.dao.qa_log_dao import insert_qa_log
from hri.domain.schemas import QALog


# ----------------------------------------------------------------
# 1. 메모리 DB 만들기 (FK는 schema.sql과 같게)
# ----------------------------------------------------------------
def dict_row(cursor, row):
	"""pymysql DictCursor처럼 줄마다 딕셔너리로 돌려주기"""
	result = {}
	for i in range(len(cursor.description)):
		result[cursor.description[i][0]] = row[i]
	return result


db = sqlite3.connect(":memory:")
db.row_factory = dict_row
db.execute("PRAGMA foreign_keys = ON")

db.executescript("""
	CREATE TABLE users (
		user_id TEXT PRIMARY KEY, name TEXT, password TEXT, role TEXT DEFAULT 'user'
	);
	CREATE TABLE chat_sessions (
		session_id TEXT PRIMARY KEY,
		user_id TEXT NOT NULL,
		status TEXT NOT NULL DEFAULT 'active',
		created_at TEXT DEFAULT CURRENT_TIMESTAMP,
		closed_at TEXT NULL,
		FOREIGN KEY (user_id) REFERENCES users (user_id)
	);
	CREATE TABLE qa_logs (
		log_id INTEGER PRIMARY KEY AUTOINCREMENT,
		user_id TEXT NOT NULL, session_id TEXT NOT NULL, question TEXT, answer TEXT, persona TEXT,
		route TEXT, has_source INTEGER, response_ms INTEGER, tokens INTEGER, cost_krw REAL,
		feedback INTEGER NULL, error TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
		FOREIGN KEY (user_id) REFERENCES users (user_id),
		FOREIGN KEY (session_id) REFERENCES chat_sessions (session_id)
	);
	INSERT INTO users (user_id, name, password) VALUES ('kim', '김', 'pw');
""")


class FakeCursor:
	"""MySQL 문법을 SQLite 문법으로 바꿔서 실행해 주는 커서"""

	def __init__(self):
		self.cursor = db.cursor()
		self.lastrowid = None

	def execute(self, sql, values=()):
		sql = sql.replace("%s", "?")
		sql = sql.replace("ON DUPLICATE KEY UPDATE session_id = session_id", "ON CONFLICT (session_id) DO NOTHING")
		self.cursor.execute(sql, values)
		self.lastrowid = self.cursor.lastrowid


class FakeConnection:
	"""with get_connection() as conn: 모양 그대로 쓸 수 있게 감싼 것"""

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return False

	def cursor(self):
		return FakeCursor()

	def commit(self):
		db.commit()


qa_log_dao.get_connection = lambda: FakeConnection()


def make_log(session_id, question, route="rag", error=None):
	"""테스트용 질문 기록"""
	return QALog(
		user_id="kim", session_id=session_id, question=question, answer="답변",
		persona="실무자", route=route, has_source=False, response_ms=100,
		tokens=0, cost_krw=0.0, error=error
	)


def count(sql):
	return db.execute(sql).fetchone()["n"]


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
# 3. 수정 전 문제 재현: 세션 줄 없이 qa_logs에 바로 넣으면 FK 위반
# ----------------------------------------------------------------
try:
	db.execute("INSERT INTO qa_logs (user_id, session_id, question, answer, route) VALUES ('kim', '없는-세션', 'q', 'a', 'rag')")
	check("1 (수정 전 재현) 세션 줄 없이 저장하면 FK 위반", False)
except sqlite3.IntegrityError as e:
	check("1 (수정 전 재현) 세션 줄 없이 저장하면 FK 위반", True, e)

# ----------------------------------------------------------------
# 4. 수정 후 insert_qa_log
# ----------------------------------------------------------------
log_id_1 = insert_qa_log(make_log("s-1", "기준금리 전망은?"))
check("2 새 대화 첫 질문: 에러 없이 저장, log_id 반환", log_id_1 == 1, log_id_1)
check("3 chat_sessions에 s-1 줄이 생김 (status='active')", count("SELECT COUNT(*) AS n FROM chat_sessions WHERE session_id = 's-1' AND status = 'active'") == 1)

log_id_2 = insert_qa_log(make_log("s-1", "그 이유는?"))
check("4 같은 대화 두 번째 질문: 에러 없이 저장, log_id 2", log_id_2 == 2, log_id_2)
check("5 chat_sessions의 s-1은 그대로 1줄", count("SELECT COUNT(*) AS n FROM chat_sessions WHERE session_id = 's-1'") == 1)
check("6 qa_logs의 s-1 기록은 2줄", count("SELECT COUNT(*) AS n FROM qa_logs WHERE session_id = 's-1'") == 2)

log_id_3 = insert_qa_log(make_log("s-2", "", route="error", error="빈 질문"))
check("7 오류 기록(_save_error_log가 넣는 route='error')도 새 대화에 저장", log_id_3 == 3, log_id_3)
check("8 chat_sessions는 대화 2개 (s-1, s-2)", count("SELECT COUNT(*) AS n FROM chat_sessions") == 2)

saved = db.execute("SELECT log_id, session_id, question, route FROM qa_logs WHERE log_id = ?", (log_id_2,)).fetchone()
check("9 돌려준 log_id가 qa_logs의 그 질문 번호와 같음", saved["question"] == "그 이유는?" and saved["session_id"] == "s-1", saved)

print(f"\n통과 {passed}/{total}")