"""4.1.3 개인정보 마스킹 점검 — qa_logs 저장값 확인 스크립트 (서동안)

팀 DB의 qa_logs에 저장된 질문·답변에 가리지 않은 이름·아이디·비밀번호가 남아 있는지 찾는다.
mask_pii()와 같은 정규식(NAME_RE·ID_RE·PASSWORD_RE, 기능 명세서 v6)으로 '이름: 홍길동'처럼 라벨이 붙은 값을 찾고,
값에 '*'가 없으면(가려지지 않았으면) '노출'로 센다. 읽기(SELECT)만 한다.

보고서에는 노출된 값 자체를 적지 않는다 (log_id·칸·종류·라벨·값 글자 수만 적음 — 결과 파일이 또 다른 유출이 되지 않게).

실행 (프로젝트 루트, .env·SSH 터널 필요)
	poetry run python -m tests.check_qa_logs_masking              # 전체 기록
	poetry run python -m tests.check_qa_logs_masking --days 7     # 최근 7일만
결과: logs/check_masking/check_masking_YYYYMMDD_HHMM.md

함께 볼 것: tests/test_mask_pii.py (마스킹 규칙 단위 테스트), 화면 점검 '기록' 항목(내 기록 복원 화면에 '홍*동'으로 보이는지)
"""

import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path

DEFAULT_OUT_DIR = Path("logs/check_masking")
COLUMNS = ("question", "answer")


def load_rows(days: int | None) -> list[dict]:
	from hri.dao.db import get_connection

	sql = "SELECT log_id, user_id, route, question, answer, created_at FROM qa_logs"
	params: list = []
	if days:
		sql += " WHERE created_at >= NOW() - INTERVAL %s DAY"
		params.append(days)
	sql += " ORDER BY log_id"
	with get_connection() as conn:
		with conn.cursor() as cur:
			cur.execute(sql, params)
			return list(cur.fetchall())


def find_leaks(text: str) -> tuple[list[dict], int]:
	"""(가리지 않은 값 목록, 가린 흔적 수). 값 자체는 돌려주지 않고 종류·라벨·글자 수만 돌려준다."""
	from hri.service.qa_service import ID_RE, NAME_RE, PASSWORD_RE

	leaks, masked = [], 0
	for kind, regex in (("이름", NAME_RE), ("아이디", ID_RE), ("비밀번호", PASSWORD_RE)):
		for match in regex.finditer(text or ""):
			value = match.group(2)
			after = (text or "")[match.end():match.end() + 1]
			if "*" in value or after == "*":
				masked += 1
				continue
			leaks.append({"kind": kind, "label": match.group(1).strip(), "length": len(value)})
	return leaks, masked


def main() -> None:
	parser = argparse.ArgumentParser(description="4.1.3 qa_logs 개인정보 마스킹 점검 (읽기 전용)")
	parser.add_argument("--days", type=int, help="최근 N일 기록만 본다 (기본: 전체)")
	parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
	args = parser.parse_args()

	rows = load_rows(args.days)
	leak_rows, by_kind, masked_total = [], Counter(), 0
	for row in rows:
		for column in COLUMNS:
			leaks, masked = find_leaks(row[column])
			masked_total += masked
			for leak in leaks:
				by_kind[leak["kind"]] += 1
				leak_rows.append({"log_id": row["log_id"], "column": column, "route": row["route"], **leak})

	lines = ["# 4.1.3 qa_logs 개인정보 마스킹 점검", "",
		f"실행일 {datetime.now():%Y-%m-%d %H:%M} · 대상 기록 {len(rows)}건" + (f" (최근 {args.days}일)" if args.days else " (전체)"), "",
		"| 항목 | 값 |", "|---|---|",
		f"| 점검한 칸 (질문·답변) | {len(rows) * len(COLUMNS)} |",
		f"| 가린 흔적 (라벨 뒤 값에 *) | {masked_total} |",
		f"| **노출 (가리지 않은 값)** | **{len(leak_rows)}** |"]
	lines += [f"| 노출 · {kind} | {by_kind.get(kind, 0)} |" for kind in ("이름", "아이디", "비밀번호")]
	lines += ["", f"**판정: {'통과 (노출 0건)' if not leak_rows else '실패 — 아래 기록 확인'}**", ""]
	if leak_rows:
		lines += ["| log_id | 칸 | route | 종류 | 라벨 | 값 글자 수 |", "|---|---|---|---|---|---|"]
		lines += [f"| {r['log_id']} | {r['column']} | {r['route']} | {r['kind']} | {r['label']} | {r['length']} |" for r in leak_rows]
	if masked_total == 0:
		lines += ["", "참고: 가린 흔적이 0건이면 마스킹 대상 질문이 아직 없는 것이다. '이름: 홍길동 아이디: user01 비밀번호: abc123'처럼 질문해 본 뒤 다시 실행한다."]

	args.out_dir.mkdir(parents=True, exist_ok=True)
	path = args.out_dir / f"check_masking_{datetime.now():%Y%m%d_%H%M}.md"
	path.write_text("\n".join(lines) + "\n", encoding="utf-8")
	print("\n".join(lines))
	print(f"\n저장: {path}")


if __name__ == "__main__":
	main()