"""4.1.6 로그인·기록·대시보드 SQL 수치 검증 스크립트 (서동안)

DB에 이미 쌓인 기록(qa_logs·llm_usage·users)을 그대로 읽어 파이썬으로 다시 계산하고,
dashboard_dao·qa_log_dao·user_dao의 SQL 결과와 같은지 비교한다.
읽기(SELECT)만 하므로 팀 DB에서 돌려도 데이터가 바뀌지 않는다.

비교 항목
	대시보드 1차: 질문 TOP 10 / 자료 없음 비율 / 일자·flow·모델별 토큰과 일평균 응답 시간 / 최근 에러 20건
	대시보드 2차: 일별 질문 수·평균 응답 시간(VIEW) / 페르소나별 질문 수(ROLLUP) / 피드백 비율 / 근거 없는 질문 /
	            처리 방식(route)별 질문 수(ROLLUP)
	로그인: select_user()가 users와 같은 이름·역할을 돌려주는지, 없는 아이디는 None·login()은 LoginFailedError인지
	기록: select_logs()가 사용자·대화별로 빠짐없이 최신순으로 돌려주는지
	참고: 대시보드 '평균 응답 시간'(일평균의 평균, LLM 사용 기록이 있는 날만)과 전체 평균의 차이, DB 시간대

실행 (프로젝트 루트에서. .env의 MySQL에 접속할 수 있어야 함 — SSH 터널 등)
	poetry run python -m tests.check_dashboard_sql
결과는 logs/check_dashboard/에 Markdown으로 저장한다 (logs/는 .gitignore 대상).
"""

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

DEFAULT_OUT_DIR = Path("logs/check_dashboard")
NO_SUCH_USER = "__4_1_7_no_such_user__"


# ---------------------------------------------------------------------
# 1. 원본 기록 읽기 (SELECT만)
# ---------------------------------------------------------------------
def fetch_all(sql: str) -> list[dict]:
	from hri.dao.db import get_connection

	with get_connection() as conn:
		with conn.cursor() as cur:
			cur.execute(sql)
			return list(cur.fetchall())


def load_raw() -> dict:
	return {
		"qa": fetch_all("SELECT log_id, user_id, session_id, question, persona, route, response_ms, feedback, error, created_at FROM qa_logs"),
		"usage": fetch_all("SELECT flow, model, input_tokens, output_tokens, created_at FROM llm_usage"),
		"users": fetch_all("SELECT user_id, name, role FROM users"),
		"tz": fetch_all("SELECT @@session.time_zone AS tz, NOW() AS db_now")[0],
	}


# ---------------------------------------------------------------------
# 2. 비교 도우미
# ---------------------------------------------------------------------
class Report:
	def __init__(self):
		self.rows: list[tuple[str, str, str, str, bool]] = []  # (묶음, 항목, SQL, 파이썬, 일치)
		self.notes: list[str] = []

	def check(self, group: str, name: str, sql_value, py_value, ok: bool | None = None) -> None:
		ok = (sql_value == py_value) if ok is None else ok
		self.rows.append((group, name, _short(sql_value), _short(py_value), ok))
		print(f"[{'일치' if ok else '불일치'}] {group} · {name}")

	def note(self, text: str) -> None:
		self.notes.append(text)
		print(f"[참고] {text}")


def _short(value, limit: int = 120) -> str:
	text = str(value).replace("|", "/").replace("\n", " ")
	return text if len(text) <= limit else text[:limit] + "…"


def _avg(values: list[float]) -> float | None:
	values = [v for v in values if v is not None]
	return round(sum(values) / len(values), 1) if values else None


def _day(value):
	return value.date() if isinstance(value, datetime) else value


# ---------------------------------------------------------------------
# 3. 대시보드 1차
# ---------------------------------------------------------------------
def check_dashboard_v1(raw: dict, report: Report) -> None:
	from hri.dao import dashboard_dao

	qa, usage = raw["qa"], raw["usage"]
	group = "대시보드 1차"

	# 질문 TOP 10: 같은 횟수(동점)는 순서가 정해지지 않으므로 횟수만 비교한다
	sql_top = dashboard_dao.select_top_questions()
	counts = Counter(r["question"] for r in qa)
	expected_len = min(10, len(counts))
	ok = (len(sql_top) == expected_len
		and all(counts[r["question"]] == r["count"] for r in sql_top)
		and [r["count"] for r in sql_top] == sorted(counts.values(), reverse=True)[:expected_len])
	report.check(group, "질문 TOP 10 (질문별 횟수·내림차순)", [(r["question"], r["count"]) for r in sql_top],
		sorted(counts.items(), key=lambda kv: -kv[1])[:expected_len], ok)
	if sql_top and len(sql_top) == 10 and len([c for c in counts.values() if c == sql_top[-1]["count"]]) > 1:
		report.note("TOP 10 마지막 횟수에 동점 질문이 여러 개라, 어떤 질문이 10위에 들어갈지는 실행마다 달라질 수 있음 (ORDER BY에 두 번째 기준 없음)")

	# 자료 없음 비율: rag·no_answer 중 no_answer
	target = [r for r in qa if r["route"] in ("rag", "no_answer")]
	py_ratio = round(sum(r["route"] == "no_answer" for r in target) / len(target), 4) if target else 0.0
	sql_ratio = dashboard_dao.select_source_ratio()
	report.check(group, "자료 없음 비율 (rag·no_answer만)", round(sql_ratio, 4), py_ratio, abs(sql_ratio - py_ratio) < 0.0001)

	# 일자·flow·모델별 토큰 + 그날 평균 응답 시간
	sums = defaultdict(lambda: [0, 0])
	for u in usage:
		key = (_day(u["created_at"]), u["flow"], u["model"])
		sums[key][0] += u["input_tokens"]
		sums[key][1] += u["output_tokens"]
	day_ms = defaultdict(list)
	for r in qa:
		day_ms[_day(r["created_at"])].append(r["response_ms"])
	py_perf = {k: (v[0], v[1], _avg(day_ms.get(k[0], []))) for k, v in sums.items()}
	sql_perf_rows = dashboard_dao.select_perf_cost()
	sql_perf = {(r["day"], r["flow"], r["model"]): (r["input_tokens"], r["output_tokens"], r["avg_response_ms"]) for r in sql_perf_rows}
	report.check(group, "일자·flow·모델별 토큰 합계·일평균 응답 시간", sorted(sql_perf.items()), sorted(py_perf.items()))

	# 최근 에러 20건: route='error' 또는 error 칸이 있는 기록, 최신순
	errors = sorted((r for r in qa if r["route"] == "error" or r["error"] is not None),
		key=lambda r: (r["created_at"], r["log_id"]), reverse=True)[:20]
	report.check(group, "최근 에러 20건 (log_id 순서)", [r["log_id"] for r in dashboard_dao.select_recent_errors()],
		[r["log_id"] for r in errors])

	# 참고: 대시보드 평균 응답 시간 계산 방식
	dashboard_days = {r["day"]: r["avg_response_ms"] for r in sql_perf_rows if r["avg_response_ms"] is not None}
	dashboard_avg = round(sum(dashboard_days.values()) / len(dashboard_days), 1) if dashboard_days else 0.0
	overall_avg = _avg([r["response_ms"] for r in qa]) or 0.0
	missing_days = sorted(d for d in day_ms if d not in {k[0] for k in sums})
	report.note(f"대시보드 '평균 응답 시간' = 일평균의 평균 {dashboard_avg}ms / 전체 질문 평균 {overall_avg}ms "
		f"(차이 {round(dashboard_avg - overall_avg, 1)}ms)")
	if missing_days:
		report.note(f"질문은 있지만 LLM 사용 기록(llm_usage)이 없는 날 {len(missing_days)}일은 대시보드 평균 응답 시간에서 빠짐: "
			f"{', '.join(map(str, missing_days[:5]))}{' …' if len(missing_days) > 5 else ''}")


# ---------------------------------------------------------------------
# 4. 대시보드 2차
# ---------------------------------------------------------------------
def check_dashboard_v2(raw: dict, report: Report) -> None:
	from hri.dao import dashboard_dao

	qa = raw["qa"]
	group = "대시보드 2차"

	day_rows = defaultdict(list)
	for r in qa:
		day_rows[_day(r["created_at"])].append(r["response_ms"])
	py_daily = [(d, len(v), _avg(v)) for d, v in sorted(day_rows.items())]
	sql_daily = [(r["day"], r["question_count"], r["avg_response_ms"]) for r in dashboard_dao.select_daily_counts()]
	report.check(group, "일별 질문 수·평균 응답 시간 (VIEW v_daily_usage)", sql_daily, py_daily)

	persona = Counter(r["persona"] for r in qa)
	sql_persona = dashboard_dao.select_persona_ratio()
	sql_by = {r["persona"]: r["count"] for r in sql_persona if r["persona"] is not None}
	sql_total = next((r["count"] for r in sql_persona if r["persona"] is None), 0)
	report.check(group, "페르소나별 질문 수", sorted(sql_by.items()), sorted(persona.items()))
	report.check(group, "페르소나 합계 줄 (WITH ROLLUP)", sql_total, len(qa))

	fb = [r["feedback"] for r in qa if r["feedback"] is not None and r["route"] in ("rag", "no_answer")]
	up, down = fb.count(1), fb.count(-1)
	py_fb = {"up_count": up, "down_count": down, "total": len(fb),
		"up_ratio": round(up / len(fb), 4) if fb else 0.0, "down_ratio": round(down / len(fb), 4) if fb else 0.0}
	report.check(group, "피드백 👍/👎 (잡담·오류 제외)", dashboard_dao.select_feedback_ratio(), py_fb)

	no_source = Counter(r["question"] for r in qa if r["route"] == "no_answer")
	py_ns = sorted((q, c) for q, c in no_source.items() if c >= 2)
	sql_ns = sorted((r["question"], r["count"]) for r in dashboard_dao.select_no_source_questions())
	report.check(group, "근거 없는 질문 (no_answer 2회 이상)", sql_ns, py_ns)

	# 처리 방식(route)별 질문 수 + 합계 줄 (WITH ROLLUP, 합계 줄은 route=None)
	routes = Counter(r["route"] for r in qa)
	sql_route = dashboard_dao.select_route_ratio()
	sql_by_route = {r["route"]: r["count"] for r in sql_route if r["route"] is not None}
	sql_route_total = next((r["count"] for r in sql_route if r["route"] is None), 0)
	report.check(group, "처리 방식(route)별 질문 수", sorted(sql_by_route.items()), sorted(routes.items()))
	report.check(group, "처리 방식 합계 줄 (WITH ROLLUP)", sql_route_total, len(qa))


# ---------------------------------------------------------------------
# 5. 로그인·기록
# ---------------------------------------------------------------------
def check_login_history(raw: dict, report: Report) -> None:
	from hri.dao.qa_log_dao import select_logs
	from hri.dao.user_dao import select_user
	from hri.exception.errors import LoginFailedError
	from hri.service.user_service import login

	group = "로그인"
	wrong = []
	for u in raw["users"]:
		found = select_user(u["user_id"])
		if found is None or (found.name, found.role) != (u["name"], u["role"]):
			wrong.append(u["user_id"])
	report.check(group, f"select_user() 이름·역할 (회원 {len(raw['users'])}명)", wrong, [])
	report.check(group, "없는 아이디는 select_user() = None", select_user(NO_SUCH_USER), None)
	null_ms = [r["log_id"] for r in raw["qa"] if r["response_ms"] is None]
	if null_ms:
		report.note(f"response_ms가 NULL인 기록 {len(null_ms)}건(log_id {null_ms[:5]}) — DB는 NULL을 허용하지만 QALog는 int만 받아 "
			"그 사용자의 select_logs()가 실패함 (앱은 항상 숫자로 저장하므로 직접 넣은 데이터에서만 생김)")
	zero_ms = sum(1 for r in raw["qa"] if r["response_ms"] == 0)
	if zero_ms:
		report.note(f"response_ms가 0인 기록 {zero_ms}건 — started_at 없이 저장된 기록이면 평균 응답 시간을 낮춤")
	try:
		login(NO_SUCH_USER, "x")
		failed = False
	except LoginFailedError:
		failed = True
	report.check(group, "없는 아이디로 login() → LoginFailedError", failed, True)

	group = "기록"
	by_user = defaultdict(list)
	for r in raw["qa"]:
		by_user[r["user_id"]].append(r)
	user_wrong, session_wrong, crashed = [], [], []

	def ids(user_id, session_id=None):
		# 기록 한 줄이라도 QALog로 못 바꾸면(예: response_ms가 NULL) select_logs() 전체가 실패한다
		try:
			return [log.log_id for log in select_logs(user_id, session_id)]
		except Exception as e:
			crashed.append(f"{user_id}{'/' + session_id if session_id else ''}: {type(e).__name__}")
			return None

	for user_id, rows in by_user.items():
		expected = [r["log_id"] for r in sorted(rows, key=lambda r: (r["created_at"], r["log_id"]), reverse=True)]
		got = ids(user_id)
		if got is not None and got != expected:
			user_wrong.append(user_id)
		for session_id in {r["session_id"] for r in rows}:
			exp = [r["log_id"] for r in sorted((r for r in rows if r["session_id"] == session_id),
				key=lambda r: (r["created_at"], r["log_id"]), reverse=True)]
			got = ids(user_id, session_id)
			if got is not None and got != exp:
				session_wrong.append(f"{user_id}/{session_id}")
	report.check(group, "select_logs() 오류 없이 읽힘", crashed, [])
	report.check(group, f"사용자별 기록 개수·최신순 (사용자 {len(by_user)}명)", user_wrong, [])
	report.check(group, "대화(session)별 기록 개수·최신순", session_wrong, [])
	report.check(group, "기록 없는 사용자는 빈 목록", select_logs(NO_SUCH_USER), [])


# ---------------------------------------------------------------------
# 6. 실행
# ---------------------------------------------------------------------
def to_markdown(report: Report, raw: dict) -> str:
	ok = sum(r[4] for r in report.rows)
	lines = ["# 4.1.6 로그인·기록·대시보드 SQL 수치 검증", "",
		f"실행일 {datetime.now():%Y-%m-%d %H:%M} · qa_logs {len(raw['qa'])}건 · llm_usage {len(raw['usage'])}건 · users {len(raw['users'])}명", "",
		f"**결과: {ok}/{len(report.rows)} 일치**", "",
		"| 묶음 | 항목 | SQL 결과 | 파이썬 재계산 | 일치 |", "|---|---|---|---|---|"]
	for group, name, sql_value, py_value, matched in report.rows:
		lines.append(f"| {group} | {name} | {sql_value} | {py_value} | {'O' if matched else '**X**'} |")
	lines += ["", "## 참고", ""] + [f"- {n}" for n in report.notes]
	return "\n".join(lines) + "\n"


def main() -> None:
	raw = load_raw()
	report = Report()
	tz = raw["tz"]
	report.note(f"DB 시간대 {tz['tz']}, DB 현재 시각 {tz['db_now']} / 이 컴퓨터 {datetime.now():%Y-%m-%d %H:%M:%S} "
		"(시각이 9시간 다르면 DB가 UTC라 '일별' 집계의 날짜 경계가 한국 시간과 어긋남)")
	check_dashboard_v1(raw, report)
	check_dashboard_v2(raw, report)
	check_login_history(raw, report)

	DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
	path = DEFAULT_OUT_DIR / f"check_dashboard_{datetime.now():%Y%m%d_%H%M}.md"
	text = to_markdown(report, raw)
	path.write_text(text, encoding="utf-8")
	print("\n" + text + f"\n저장: {path}")


if __name__ == "__main__":
	main()