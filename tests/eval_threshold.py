"""4.1.2 할루시네이션·THRESHOLD 점검 스크립트 (서동안)

질의응답 설계서(docs/qa_design.md) 4장 '값을 정하는 방법'을 그대로 실행한다.
	1. 보고서 안 질문 30개(4.1.1 정답셋)와 밖 질문 16개(qa_out_of_scope.json)를 준비한다.
	2. 질문마다 1등 검색 결과의 score를 기록한다.
	3. 안쪽 질문의 최저 score와 바깥쪽 질문의 최고 score 사이에서 기준값을 정한다.
	4. 두 범위가 겹치면 지어냄을 줄이는 쪽(높은 값)을 우선한다.

1등 score 두 가지
	- 원 질문: 원 질문 벡터의 의미 검색 1등 score (임베딩만 사용, 저렴)
	- 확장 포함(--expansion): hybrid_search처럼 원 질문 + expand_queries() 3개 중 가장 높은 1등 score.
	  safe_search는 질문별로 THRESHOLD를 적용하므로, 확장 질문 하나라도 기준을 넘으면 근거가 생긴다.
	  실제 근거 없음 판정과 같은 기준이라 THRESHOLD를 정할 때는 이 값을 쓴다 (질문마다 LLM 1회 추가).

할루시네이션 점검(--run-out)
	밖 질문 16개를 현재 THRESHOLD로 실제 실행(4.1.1과 같은 그래프 우회 방식)해 답변을 기록한다.
	route=no_answer이거나 답변이 '자료에 없습니다'면 통과, 다른 내용으로 답하면 할루시네이션 후보.
	검색을 통과한 근접 함정 질문에서 few-shot ②·규칙 system이 '자료에 없습니다'를 이끄는지 확인한다.

실행 (프로젝트 루트에서. OpenAI API·MySQL·Chroma 사용)
	poetry run python -m tests.eval_threshold --dry-run                 # 질문·report_id 확인 (API 없음)
	poetry run python -m tests.eval_threshold                           # 원 질문 score만 (임베딩 비용만)
	poetry run python -m tests.eval_threshold --expansion --run-out     # 권장: 확장 포함 score + 밖 질문 답변
	poetry run python -m tests.eval_threshold --thresholds 0.20:0.70:0.05 --report-ids FX=3,PR=4,GR=5

결과는 logs/eval_threshold/에 저장한다 (logs/는 .gitignore 대상).
확정한 값은 .env의 THRESHOLD와 qa_design.md 4장 표에 옮긴다.
"""

import argparse
from datetime import datetime
from pathlib import Path

from tests.eval_qa_accuracy import (
	DEFAULT_ANSWERS,
	_normalize,
	load_answers,
	resolve_report_ids,
	run_question,
	write_csv,
)

DEFAULT_OUT_OF_SCOPE = Path("tests/fixtures/qa_out_of_scope.json")
DEFAULT_OUT_DIR = Path("logs/eval_threshold")
NO_ANSWER_NORMALIZED = _normalize("자료에 없습니다")

SCORE_COLUMNS = ["구분", "번호", "유형·범주", "보고서", "질문", "1등 score(원 질문)", "1등 score(확장 포함)",
	"1등 위치", "1등을 만든 질문", "현재 THRESHOLD 판정"]
ANSWER_COLUMNS = ["번호", "범주", "보고서", "질문", "근거 없는 이유", "route", "답변", "판정(자동 후보)", "메모"]


# ---------------------------------------------------------------------
# 1. 질문 모으기
# ---------------------------------------------------------------------
def collect_questions(in_data: dict, out_data: dict, ids: dict[str, int]) -> list[dict]:
	"""보고서 안(in) 30개 + 밖(out) 16개를 한 목록으로. report_ids는 질문할 때 선택하는 보고서."""
	rows = []
	for item in in_data["items"]:
		rows.append({"side": "안", "id": item["id"], "group": item["type"], "reports": item["reports"],
			"report_ids": [ids[c] for c in item["reports"]], "question": item["question"]})
	for item in out_data["items"]:
		rows.append({"side": "밖", "id": item["id"], "group": item["category"], "reports": item["reports"],
			"report_ids": [ids[c] for c in item["reports"]], "question": item["question"]})
	return rows


# ---------------------------------------------------------------------
# 2. 1등 score 측정
# ---------------------------------------------------------------------
def measure_scores(questions: list[dict], expansion: bool, code_of: dict[int, str]) -> None:
	"""질문마다 원 질문·확장 포함 1등 score와 1등 청크 위치를 채운다 (questions를 직접 고침)."""
	from hri.retriever.search import expand_queries, vector_search
	from hri.vector_store.embedding import embed_texts

	# 질문별 검색 문장 목록: [원 질문] 또는 [원 질문 + 확장 3개]
	query_lists = []
	for n, q in enumerate(questions, 1):
		queries = [q["question"]]
		if expansion:
			queries += expand_queries(q["question"])
			print(f"[확장 {n:>2}/{len(questions)}] {q['id']} {len(queries) - 1}개")
		query_lists.append(queries)

	# 임베딩은 한 번에 (API 호출 수를 줄임)
	flat = [text for queries in query_lists for text in queries]
	vectors = iter(embed_texts(flat))

	for q, queries in zip(questions, query_lists):
		best = []  # (score, 위치, 검색 문장)
		for text in queries:
			hits = vector_search(next(vectors), k=1, report_ids=q["report_ids"])
			if hits:
				hit = hits[0]
				where = f"{code_of.get(hit.chunk.report_id, hit.chunk.report_id)} p.{hit.chunk.page} ({hit.chunk.chunk_type})"
				best.append((round(hit.score, 4), where, text))
		q["score_orig"] = best[0][0] if best and best[0][2] == q["question"] else None
		top = max(best, key=lambda b: b[0]) if best else (None, "", "")
		q["score_eff"] = top[0]
		q["where"] = top[1]
		q["best_query"] = "" if top[2] == q["question"] else top[2]


# ---------------------------------------------------------------------
# 3. THRESHOLD 후보별 비교 · 추천
# ---------------------------------------------------------------------
def parse_range(text: str) -> list[float]:
	"""'0.20:0.60:0.05' → [0.2, 0.25, ..., 0.6]"""
	start, stop, step = (float(v) for v in text.split(":"))
	values, t = [], start
	while t <= stop + 1e-9:
		values.append(round(t, 4))
		t += step
	return values


def sweep(questions: list[dict], thresholds: list[float]) -> list[dict]:
	"""기준값마다 안 질문 통과율(근거를 찾음)과 밖 질문 차단율(근거 없음 처리)을 계산한다."""
	ins = [q["score_eff"] for q in questions if q["side"] == "안" and q["score_eff"] is not None]
	outs = [q["score_eff"] for q in questions if q["side"] == "밖" and q["score_eff"] is not None]
	rows = []
	for t in thresholds:
		in_pass = sum(s >= t for s in ins)
		out_block = sum(s < t for s in outs)
		rows.append({"t": t, "in_pass": in_pass, "in_total": len(ins), "out_block": out_block, "out_total": len(outs)})
	return rows


def recommend(questions: list[dict], table: list[dict], min_recall: float) -> tuple[float | None, str]:
	"""qa_design 4장 규칙으로 추천값을 고른다. 최종 결정은 사람이 한다.

	- 두 범위가 안 겹치면: 안 질문 최저와 밖 질문 최고의 중간값
	- 겹치면: 지어냄을 줄이는 쪽(높은 값)을 우선하되, 안 질문을 너무 많이 잃지 않게
	  '안 질문 근거 찾음 비율 ≥ min_recall'인 후보 중 가장 높은 값
	"""
	ins = [q["score_eff"] for q in questions if q["side"] == "안" and q["score_eff"] is not None]
	outs = [q["score_eff"] for q in questions if q["side"] == "밖" and q["score_eff"] is not None]
	if not ins or not outs:
		return None, "score가 없어 추천할 수 없음"
	min_in, max_out = min(ins), max(outs)
	if max_out < min_in:
		t = round((min_in + max_out) / 2, 2)
		return t, f"두 범위가 겹치지 않음 → 사이 중간값 (밖 최고 {max_out:.3f} < {t} ≤ 안 최저 {min_in:.3f})"

	allowed = [r for r in table if r["in_pass"] / r["in_total"] >= min_recall]
	if not allowed:
		return None, f"안 질문을 {min_recall:.0%} 이상 살리는 후보가 없음 → --thresholds 범위를 낮춰 다시 실행"
	r = allowed[-1]  # table은 낮은 값부터 정렬되어 있으므로 마지막이 가장 높은 값
	return r["t"], (f"두 범위가 겹침 → 안 질문 근거 찾음 {min_recall:.0%} 이상을 지키는 가장 높은 값 "
		f"(안 {r['in_pass']}/{r['in_total']}, 밖 {r['out_block']}/{r['out_total']} 차단). "
		"남는 밖 질문(주로 근접 함정)은 검색으로는 못 막으니 --run-out 결과로 LLM 규칙이 '자료에 없습니다'를 내는지 확인")


# ---------------------------------------------------------------------
# 4. 밖 질문 실제 실행 (할루시네이션 점검)
# ---------------------------------------------------------------------
def is_refusal(route: str, answer: str) -> bool:
	return route == "no_answer" or NO_ANSWER_NORMALIZED in _normalize(answer)


def run_out_of_scope(out_data: dict, ids: dict[str, int], persona: str, k: int) -> list[dict]:
	rows = []
	items = out_data["items"]
	for n, item in enumerate(items, 1):
		row = {"번호": item["id"], "범주": item["category"], "보고서": "+".join(item["reports"]),
			"질문": item["question"], "근거 없는 이유": item["why_absent"]}
		try:
			result = run_question(item["question"], [ids[c] for c in item["reports"]], persona, k, False)
			ok = is_refusal(result["route"], result["answer"])
			row.update({"route": result["route"], "답변": result["answer"],
				"판정(자동 후보)": "통과" if ok else "할루시네이션", "메모": "자동 판정 · 답변 확인 필요"})
		except Exception as error:
			row.update({"route": "error", "판정(자동 후보)": "", "메모": f"실행 오류: {type(error).__name__}: {error}"})
		rows.append(row)
		print(f"[밖 {n:>2}/{len(items)}] {item['id']} route={row['route']:<9} {row['판정(자동 후보)']}")
	return rows


# ---------------------------------------------------------------------
# 5. 요약
# ---------------------------------------------------------------------
def build_summary(questions, table, rec, rec_note, current, expansion, answers, conditions) -> str:
	ins = [q for q in questions if q["side"] == "안" and q["score_eff"] is not None]
	outs = [q for q in questions if q["side"] == "밖" and q["score_eff"] is not None]
	basis = "확장 포함" if expansion else "원 질문"
	min_in = min(ins, key=lambda q: q["score_eff"]) if ins else None
	max_out = max(outs, key=lambda q: q["score_eff"]) if outs else None

	lines = ["# 4.1.2 할루시네이션·THRESHOLD 점검 결과", "", conditions, "",
		f"판단 기준 score: **{basis}** 1등 score", "",
		"## qa_design.md 4장 표에 옮길 값", "", "| 측정 결과 | 값 |", "|---|---|",
		f"| 보고서 안 질문의 최저 score | {min_in['score_eff']:.3f} ({min_in['id']}) |" if min_in else "| 보고서 안 질문의 최저 score | - |",
		f"| 보고서 밖 질문의 최고 score | {max_out['score_eff']:.3f} ({max_out['id']}) |" if max_out else "| 보고서 밖 질문의 최고 score | - |",
		f"| 추천 THRESHOLD | {rec if rec is not None else '-'} (현재 {current}) |", "",
		f"추천 근거: {rec_note}", "",
		"## THRESHOLD 후보별 비교", "",
		"| THRESHOLD | 안 질문 근거 찾음 | 밖 질문 근거 없음 처리 | 비고 |", "|---|---|---|---|"]
	for r in table:
		note = "현재" if abs(r["t"] - current) < 1e-9 else ("추천" if rec is not None and abs(r["t"] - rec) < 1e-9 else "")
		lines.append(f"| {r['t']:.2f} | {r['in_pass']}/{r['in_total']} | {r['out_block']}/{r['out_total']} | {note} |")

	lines += ["", "## 밖 질문 중 score가 높은 순 (기준을 넘기 쉬운 질문)", "",
		"| 번호 | 범주 | score | 1등 위치 | 질문 |", "|---|---|---|---|---|"]
	for q in sorted(outs, key=lambda q: q["score_eff"], reverse=True)[:6]:
		lines.append(f"| {q['id']} | {q['group']} | {q['score_eff']:.3f} | {q['where']} | {q['question']} |")

	lines += ["", "## 안 질문 중 score가 낮은 순 (기준에 걸리기 쉬운 질문)", "",
		"| 번호 | 유형 | score | 1등 위치 | 질문 |", "|---|---|---|---|---|"]
	for q in sorted(ins, key=lambda q: q["score_eff"])[:6]:
		lines.append(f"| {q['id']} | {q['group']} | {q['score_eff']:.3f} | {q['where']} | {q['question']} |")

	if answers:
		lines += ["", f"## 할루시네이션 점검 (밖 질문 실제 실행, THRESHOLD {current})", "",
			"| 범주 | 문항 | 통과 | 할루시네이션 | 그중 검색 통과(rag) 후 LLM이 막음 |", "|---|---|---|---|---|"]
		for cat in dict.fromkeys(a["범주"] for a in answers):
			group = [a for a in answers if a["범주"] == cat]
			ok = sum(a["판정(자동 후보)"] == "통과" for a in group)
			bad = sum(a["판정(자동 후보)"] == "할루시네이션" for a in group)
			llm = sum(a["route"] == "rag" and a["판정(자동 후보)"] == "통과" for a in group)
			lines.append(f"| {cat} | {len(group)} | {ok} | {bad} | {llm} |")
		total_bad = [a["번호"] for a in answers if a["판정(자동 후보)"] == "할루시네이션"]
		lines += ["", f"할루시네이션 후보: {', '.join(total_bad) if total_bad else '없음'}"]
	return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------
# 6. 실행
# ---------------------------------------------------------------------
def main() -> None:
	parser = argparse.ArgumentParser(description="4.1.2 할루시네이션·THRESHOLD 점검")
	parser.add_argument("--answers", type=Path, default=DEFAULT_ANSWERS, help="보고서 안 질문 (4.1.1 정답셋)")
	parser.add_argument("--out-of-scope", type=Path, default=DEFAULT_OUT_OF_SCOPE, help="보고서 밖 질문")
	parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
	parser.add_argument("--report-ids", help="report_id 직접 지정. 예) FX=3,PR=4,GR=5")
	parser.add_argument("--thresholds", default="0.10:0.60:0.05", help="비교할 후보 '시작:끝:간격'")
	parser.add_argument("--expansion", action="store_true", help="확장 질문까지 포함한 1등 score (권장, 질문마다 LLM 1회)")
	parser.add_argument("--run-out", action="store_true", help="밖 질문 16개를 실제 실행해 할루시네이션 점검")
	parser.add_argument("--min-recall", type=float, default=0.9, help="범위가 겹칠 때 지켜야 할 안 질문 근거 찾음 비율 (기본 0.9)")
	parser.add_argument("--persona", default="실무자")
	parser.add_argument("--k", type=int, default=4)
	parser.add_argument("--dry-run", action="store_true", help="API를 부르지 않고 질문·report_id만 확인")
	args = parser.parse_args()

	in_data = load_answers(args.answers)
	out_data = load_answers(args.out_of_scope)
	ids = resolve_report_ids(in_data, args.report_ids)
	code_of = {v: c for c, v in ids.items()}
	questions = collect_questions(in_data, out_data, ids)
	print(f"report_id: {ids} · 안 {sum(q['side'] == '안' for q in questions)}개 · 밖 {sum(q['side'] == '밖' for q in questions)}개")

	if args.dry_run:
		for q in questions:
			print(q["side"], q["id"], q["report_ids"], q["question"])
		return

	from hri.config.settings import load_settings

	settings = load_settings()
	current = settings.threshold
	stamp = datetime.now().strftime("%Y%m%d_%H%M")
	conditions = (f"실행일 {datetime.now():%Y-%m-%d %H:%M} · 현재 THRESHOLD={current} · 확장 포함={args.expansion} · "
		f"모델={settings.llm_model} · report_id={ids}")

	measure_scores(questions, args.expansion, code_of)
	thresholds = sorted(set(parse_range(args.thresholds) + [current]))
	table = sweep(questions, thresholds)
	rec, rec_note = recommend(questions, table, args.min_recall)

	score_rows = [{
		"구분": q["side"], "번호": q["id"], "유형·범주": q["group"], "보고서": "+".join(q["reports"]),
		"질문": q["question"], "1등 score(원 질문)": q["score_orig"],
		"1등 score(확장 포함)": q["score_eff"] if args.expansion else "",
		"1등 위치": q["where"], "1등을 만든 질문": q["best_query"],
		"현재 THRESHOLD 판정": "" if q["score_eff"] is None else ("근거 있음" if q["score_eff"] >= current else "근거 없음"),
	} for q in questions]
	score_path = args.out_dir / f"threshold_scores_{stamp}.csv"
	write_csv(score_path, SCORE_COLUMNS, score_rows)
	print(f"\nscore 저장: {score_path}")

	answers = []
	if args.run_out:
		answers = run_out_of_scope(out_data, ids, args.persona, args.k)
		answer_path = args.out_dir / f"out_of_scope_answers_{stamp}.csv"
		write_csv(answer_path, ANSWER_COLUMNS, answers)
		print(f"밖 질문 답변 저장: {answer_path}")

	summary = build_summary(questions, table, rec, rec_note, current, args.expansion, answers, conditions)
	summary_path = args.out_dir / f"threshold_{stamp}_요약.md"
	summary_path.write_text(summary, encoding="utf-8")
	print("\n" + summary)
	print(f"요약 저장: {summary_path}")
	print("추천값은 참고용입니다. 안 질문 손실과 할루시네이션 결과를 보고 확정한 뒤 .env의 THRESHOLD와 qa_design.md 4장 표를 고치세요.")


if __name__ == "__main__":
	main()