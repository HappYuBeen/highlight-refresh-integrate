"""4.1.1 정답 질문 30문항 정확도·출처 일치율 평가 스크립트 (서동안)

정답셋(tests/fixtures/qa_answers.json)의 질문을 하나씩 실행해 결과를 CSV로 저장하고, 요약 지표를 출력한다.
qa_graph 노드(validate·rewrite·retrieve·grade·sources)가 아직 연결 전이라 그래프를 거치지 않고,
generate 노드와 같은 순서로 이미 있는 함수를 직접 부른다.

	hybrid_search → (근거 없음이면 '자료에 없습니다', LLM 호출 없음)
	→ get_context_docs → format_docs → build_messages → get_chat_model().invoke → format_sources

결과 CSV (UTF-8 BOM이라 엑셀로 바로 열림)
	route / has_source / 1등 score(원 질문 의미 검색 1등, THRESHOLD 적용 전) / 답변 / 응답 출처 쪽
	응답시간(ms, 1등 score 측정 제외) / 토큰 / 비용(원)
	정확도 점수 · 출처 일치 · 실패 원인 → 자동 채점 '후보'. 엑셀로 열어 답변을 보고 고친 뒤 --summary로 다시 집계한다.

실행 (프로젝트 루트에서. 실제 OpenAI API·MySQL·Chroma를 사용하므로 비용이 든다)
	poetry run python -m tests.eval_qa_accuracy --dry-run                    # API 호출 없이 report_id·질문 확인
	poetry run python -m tests.eval_qa_accuracy --only Q01,Q02               # 일부만
	poetry run python -m tests.eval_qa_accuracy --personas                   # 30문항 + 페르소나 비교
	poetry run python -m tests.eval_qa_accuracy --summary logs/eval_qa/qa_results_20261028_1700.csv   # 채점한 CSV 다시 집계
	poetry run python -m tests.eval_qa_accuracy --report-ids FX=3,PR=4,GR=5  # report_id 직접 지정

결과는 기본으로 logs/eval_qa/에 저장한다 (logs/는 .gitignore 대상이라 실수로 커밋되지 않음).
표준 라이브러리(csv·json)만 써서 추가 패키지가 필요 없다.
"""

import argparse
import csv
import json
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DEFAULT_ANSWERS = Path("tests/fixtures/qa_answers.json")
DEFAULT_OUT_DIR = Path("logs/eval_qa")
NO_ANSWER_TEXT = "자료에 없습니다"  # qa_graph.NO_ANSWER_TEXT와 같은 문구

# 결과 CSV 열 (엑셀에서 사람이 읽고 고치므로 한글 머리글). --summary가 이 이름으로 다시 읽는다.
C_ID, C_REPORTS, C_TYPE, C_QUESTION, C_KEY, C_PAGES = "번호", "보고서", "유형", "질문", "정답", "정답 출처 쪽"
C_ROUTE, C_HAS, C_SCORE, C_ANSWER, C_SRC = "route", "has_source", "1등 score", "답변", "응답 출처 쪽"
C_MS, C_TOKENS, C_COST = "응답시간(ms)", "토큰", "비용(원)"
C_ACC, C_SRC_OK, C_REASON, C_MEMO = "정확도 점수", "출처 일치", "실패 원인", "메모"
RESULT_COLUMNS = [C_ID, C_REPORTS, C_TYPE, C_QUESTION, C_KEY, C_PAGES, C_ROUTE, C_HAS, C_SCORE, C_ANSWER,
	C_SRC, C_MS, C_TOKENS, C_COST, C_ACC, C_SRC_OK, C_REASON, C_MEMO]
PERSONA_COLUMNS = ["번호", "질문", "페르소나", "기대 특징", "답변", "글자 수", "형식 자동 후보",
	"말투(O/X)", "형식(O/X)", "용어 처리(O/X)", "수치·출처 정확(O/X)", "메모"]


# ---------------------------------------------------------------------
# 1. 정답셋·report_id
# ---------------------------------------------------------------------
def load_answers(path: Path) -> dict:
	with path.open(encoding="utf-8") as f:
		return json.load(f)


def resolve_report_ids(data: dict, override: str | None) -> dict[str, int]:
	"""보고서 약칭 → report_id. 우선순위: --report-ids > JSON의 report_id > 제목 키워드로 DB 검색."""
	ids = {r["code"]: r["report_id"] for r in data["reports"] if r.get("report_id") is not None}
	if override:
		for pair in override.split(","):
			code, value = pair.split("=")
			ids[code.strip()] = int(value)

	missing = [r for r in data["reports"] if r["code"] not in ids]
	if missing:
		from hri.dao.report_dao import select_reports

		for report in missing:
			found = [r for r in select_reports(report["search_keyword"]) if (r.chunk_count or 0) > 0]
			if not found:
				raise SystemExit(
					f"[중단] {report['code']}({report['title']}) 보고서를 DB에서 찾지 못했습니다. "
					f"업로드했는지 확인하고 --report-ids {report['code']}=번호 로 지정해 주세요."
				)
			if len(found) > 1:
				print(f"[주의] {report['code']}: '{report['search_keyword']}' 보고서가 {len(found)}개입니다. "
					f"가장 최근 것(report_id={found[0].report_id})을 씁니다.")
			ids[report["code"]] = found[0].report_id
	return ids


def pages_text(pages: dict[str, list[int]]) -> str:
	"""{'GR': [6, 20], 'FX': [5]} → 'GR 6, 20 / FX 5' (CSV에 사람이 읽기 좋게)"""
	return " / ".join(f"{code} {', '.join(map(str, nums))}" for code, nums in pages.items())


# ---------------------------------------------------------------------
# 2. 질문 1개 실행 (generate 노드와 같은 순서, 그래프 우회)
# ---------------------------------------------------------------------
def _text(content) -> str:
	"""AIMessage.content가 문자열이든 블록 리스트(추론 모델)든 글자만 꺼낸다."""
	if isinstance(content, str):
		return content
	return "".join(block.get("text", "") for block in content if isinstance(block, dict))


def top_score(question: str, report_ids: list[int]) -> float | None:
	"""원 질문의 의미 검색 1등 score (THRESHOLD 거르기 전). 4.1.2에서 기준값을 정할 때 쓴다."""
	from hri.retriever.search import vector_search
	from hri.vector_store.embedding import embed_texts

	hits = vector_search(embed_texts([question])[0], k=1, report_ids=report_ids)
	return round(hits[0].score, 4) if hits else None


def run_question(question: str, report_ids: list[int], persona: str, k: int, use_mmr: bool) -> dict:
	"""질문 1개를 실행해 route·답변·출처·시간·토큰·비용을 돌려준다."""
	from langchain_core.callbacks import get_usage_metadata_callback

	from hri.dao.report_dao import get_context_docs
	from hri.llm.llm_client import cost_korean_won, get_chat_model, get_usd_krw
	from hri.prompt.rag_prompt import build_messages, format_docs, has_context
	from hri.retriever.search import hybrid_search
	from hri.source.source_formatter import format_sources

	started = time.perf_counter()
	# with 안의 모든 LangChain 채팅 모델 호출(질문 확장 + 답변)의 토큰을 모델별로 모은다
	with get_usage_metadata_callback() as usage_cb:
		chunks = hybrid_search(question, k=k, report_ids=report_ids, use_mmr=use_mmr)
		if not has_context(chunks):
			answer, route, sources = NO_ANSWER_TEXT, "no_answer", []
		else:
			context_docs = get_context_docs(chunks)
			messages = build_messages(question, format_docs(context_docs), persona, [])
			answer = _text(get_chat_model().invoke(messages).content)
			route = "rag"
			sources = format_sources(context_docs)
	elapsed_ms = int((time.perf_counter() - started) * 1000)

	usage = dict(usage_cb.usage_metadata)
	tokens = sum(u.get("total_tokens", u.get("input_tokens", 0) + u.get("output_tokens", 0)) for u in usage.values())
	cost = cost_korean_won(usage, get_usd_krw()) if usage else 0.0
	return {"route": route, "answer": answer, "sources": sources, "ms": elapsed_ms, "tokens": tokens, "cost": cost}


# ---------------------------------------------------------------------
# 3. 자동 채점 (후보값. 최종은 사람이 확인)
# ---------------------------------------------------------------------
def _normalize(text: str) -> str:
	"""비교용: 공백·쉼표·%·△·마이너스 기호를 지운다. '1,440원' → '1440원', '△1.9%' → '1.9'"""
	return re.sub(r"[\s,%△\-–−]", "", text or "")


def keyword_score(answer: str, keywords: list[str], route: str) -> tuple[float, str]:
	"""키워드가 모두 있으면 1, 일부면 0.5, 없거나 근거 없음이면 0."""
	if route != "rag" or not keywords:
		return 0.0, f"키워드 0/{len(keywords)}"
	body = _normalize(answer)
	hit = sum(1 for w in keywords if _normalize(w) in body)
	score = 1.0 if hit == len(keywords) else 0.5 if hit else 0.0
	return score, f"키워드 {hit}/{len(keywords)}"


def source_match(sources: list[dict], expected: dict[str, list[int]], code_of: dict[int, str]) -> int:
	"""정답 출처의 보고서마다 응답 출처 중 하나라도 그 쪽을 포함하면 1 (여러 보고서면 모두 맞아야 1)."""
	for code, pages in expected.items():
		ok = any(
			code_of.get(s["report_id"]) == code and any(s["page_start"] <= p <= s["page_end"] for p in pages)
			for s in sources
		)
		if not ok:
			return 0
	return 1


def guess_reason(route: str, acc: float, src_ok: int) -> str:
	if route == "error":
		return "기타"
	if route == "no_answer":
		return "근거없음 오판"
	if acc < 1 and src_ok == 0:
		return "검색 실패"
	if acc < 1:
		return "답변 오류"
	if src_ok == 0:
		return "출처 오류"
	return ""


def format_source_pages(sources: list[dict], code_of: dict[int, str]) -> str:
	"""응답 출처를 'FX p.5-5; FX p.12-13' 형식으로 적는다."""
	return "; ".join(
		f"{code_of.get(s['report_id'], s['report_id'])} p.{s['page_start']}-{s['page_end']}" for s in sources
	)


# ---------------------------------------------------------------------
# 4. 요약 (실행 직후와 --summary에서 같이 씀)
# ---------------------------------------------------------------------
def _num(value) -> float | None:
	"""CSV 칸 값을 숫자로. 빈 칸·글자는 None (엑셀에서 고친 값도 읽힘)."""
	try:
		return float(value) if value not in (None, "") else None
	except ValueError:
		return None


def _rate(values: list[float]) -> str:
	return f"{sum(values) / len(values):.1%}" if values else "-"


def summarize(rows: list[dict], conditions: str = "") -> str:
	"""결과 행으로 전체·유형별·보고서별·실패 원인별 지표를 계산해 Markdown 표로 돌려준다."""
	acc = [v for r in rows if (v := _num(r[C_ACC])) is not None]
	src = [v for r in rows if (v := _num(r[C_SRC_OK])) is not None]
	scores = [v for r in rows if (v := _num(r[C_SCORE])) is not None]
	ms = [v for r in rows if (v := _num(r[C_MS])) is not None]
	tokens = [v for r in rows if (v := _num(r[C_TOKENS])) is not None]
	costs = [v for r in rows if (v := _num(r[C_COST])) is not None]

	lines = ["# 4.1.1 결과 요약", ""]
	if conditions:
		lines += [conditions, ""]
	lines += [
		"| 지표 | 값 |", "|---|---|",
		f"| 전체 문항 수 | {len(rows)} |",
		f"| 채점 문항 수 | {len(acc)} |",
		f"| **정확도** | **{_rate(acc)}** |",
		f"| **출처 일치율** | **{_rate(src)}** |",
		f"| route = rag | {sum(r[C_ROUTE] == 'rag' for r in rows)} |",
		f"| 근거없음 오판 (route = no_answer) | {sum(r[C_ROUTE] == 'no_answer' for r in rows)} |",
		f"| 실행 오류 (route = error) | {sum(r[C_ROUTE] == 'error' for r in rows)} |",
		f"| 평균 1등 score | {sum(scores) / len(scores):.3f} |" if scores else "| 평균 1등 score | - |",
		f"| 최저 1등 score (4.1.2 기준값) | {min(scores):.3f} |" if scores else "| 최저 1등 score (4.1.2 기준값) | - |",
		f"| 평균 응답시간 (ms) | {sum(ms) / len(ms):,.0f} |" if ms else "| 평균 응답시간 (ms) | - |",
		f"| 총 토큰 | {sum(tokens):,.0f} |",
		f"| 총 비용 (원) | {sum(costs):,.2f} |",
		f"| 문항당 평균 비용 (원) | {sum(costs) / len(costs):,.2f} |" if costs else "| 문항당 평균 비용 (원) | - |",
		"",
	]

	def breakdown(title: str, key: str) -> list[str]:
		groups = defaultdict(list)
		for r in rows:
			groups[r[key]].append(r)
		out = [f"## {title}", "", "| 구분 | 문항 수 | 채점 수 | 정확도 | 출처 일치율 |", "|---|---|---|---|---|"]
		for name, group in groups.items():
			g_acc = [v for r in group if (v := _num(r[C_ACC])) is not None]
			g_src = [v for r in group if (v := _num(r[C_SRC_OK])) is not None]
			out.append(f"| {name} | {len(group)} | {len(g_acc)} | {_rate(g_acc)} | {_rate(g_src)} |")
		return out + [""]

	lines += breakdown("유형별", C_TYPE)
	lines += breakdown("보고서별", C_REPORTS)

	reasons = defaultdict(int)
	for r in rows:
		if r[C_REASON]:
			reasons[r[C_REASON]] += 1
	lines += ["## 실패 원인별", "", "| 원인 | 문항 수 |", "|---|---|"]
	lines += [f"| {name} | {count} |" for name, count in reasons.items()] or ["| (없음) | 0 |"]
	return "\n".join(lines) + "\n"


def write_summary(rows: list[dict], csv_path: Path, conditions: str = "") -> None:
	text = summarize(rows, conditions)
	out = csv_path.with_name(csv_path.stem + "_요약.md")
	out.write_text(text, encoding="utf-8")
	print("\n" + text)
	print(f"요약 저장: {out}")


# ---------------------------------------------------------------------
# 5. 실행
# ---------------------------------------------------------------------
def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	with path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM: 엑셀에서 한글이 깨지지 않게
		writer = csv.DictWriter(f, fieldnames=columns)
		writer.writeheader()
		writer.writerows(rows)


def run_accuracy(data: dict, ids: dict[str, int], only: set[str] | None, persona: str, k: int, use_mmr: bool) -> list[dict]:
	code_of = {v: c for c, v in ids.items()}
	items = [i for i in data["items"] if not only or i["id"] in only]
	print(f"문항 {len(items)}개 실행 (persona={persona}, k={k}, use_mmr={use_mmr})")

	rows = []
	for n, item in enumerate(items, 1):
		report_ids = [ids[c] for c in item["reports"]]
		try:
			score = top_score(item["question"], report_ids)
			result = run_question(item["question"], report_ids, persona, k, use_mmr)
			acc, kw_note = keyword_score(result["answer"], item["keywords"], result["route"])
			src_ok = source_match(result["sources"], item["pages"], code_of) if result["route"] == "rag" else 0
			reason = guess_reason(result["route"], acc, src_ok)
			memo = f"자동 채점 후보({kw_note}) · 확인 필요"
		except Exception as error:  # 한 문항이 실패해도 나머지는 계속 돈다. 채점 칸은 비워 채점 수에서 뺀다
			score, acc, src_ok, reason = None, None, None, "기타"
			result = {"route": "error", "answer": "", "sources": [], "ms": None, "tokens": None, "cost": None}
			memo = f"실행 오류: {type(error).__name__}: {error}"

		rows.append({
			C_ID: item["id"], C_REPORTS: "+".join(item["reports"]), C_TYPE: item["type"],
			C_QUESTION: item["question"], C_KEY: item["answer"], C_PAGES: pages_text(item["pages"]),
			C_ROUTE: result["route"], C_HAS: result["route"] == "rag", C_SCORE: score,
			C_ANSWER: result["answer"], C_SRC: format_source_pages(result["sources"], code_of),
			C_MS: result["ms"], C_TOKENS: result["tokens"], C_COST: result["cost"],
			C_ACC: acc, C_SRC_OK: src_ok, C_REASON: reason, C_MEMO: memo,
		})
		print(f"[{n:>2}/{len(items)}] {item['id']} route={result['route']:<9} score={score} "
			f"정확도={acc} 출처={src_ok} {result['ms']}ms · {memo[:40]}")
	return rows


_TERM_EXPLAIN_RE = re.compile(r"\(([^()]*[가-힣]{2}[^()]*)\)")  # '용어(쉬운 풀이)'의 괄호 풀이 (출처 괄호는 따로 뺌)


def persona_format_check(persona: str, answer: str, practitioner_len: int | None) -> str:
	"""페르소나별 답변 형식 자동 후보 (persona_prompt.ANSWER_FORMATS 기준). 최종 판정은 사람이 한다.

	입문자: 괄호 용어 풀이가 1개 이상이고 모형·통계 용어(VAR·모형)가 없으면 O
	실무자: '결론:' / '근거:' / '시사점:'으로 시작하는 줄이 모두 있으면 O
	전문가: '결론:' 세 줄 형식이 아니고, 같은 질문의 실무자 답변보다 1.3배 이상 길면 O
	"""
	text = (answer or "").strip()
	if not text or text.startswith("자료에 없습니다"):
		return "-"
	if persona == "입문자":
		explains = [m for m in _TERM_EXPLAIN_RE.findall(text) if not m.startswith("출처")]
		return "O" if explains and not re.search(r"VAR|모형", text) else "X"
	if persona == "실무자":
		heads = [line.split(":")[0].strip() for line in text.splitlines() if ":" in line]
		return "O" if all(h in heads for h in ("결론", "근거", "시사점")) else "X"
	if persona == "전문가":
		deeper = practitioner_len is None or len(text) >= practitioner_len * 1.3
		return "O" if not text.startswith("결론:") and deeper else "X"
	return "-"


def run_personas(data: dict, ids: dict[str, int], k: int, use_mmr: bool) -> list[dict]:
	"""같은 질문을 세 페르소나로 돌려 답변을 모은다. 형식은 자동 후보를 적고, 말투·용어·수치는 사람이 O/X로 적는다."""
	compare = data["persona_compare"]
	by_id = {i["id"]: i for i in data["items"]}
	rows = []
	for qid in compare["question_ids"]:
		item = by_id[qid]
		report_ids = [ids[c] for c in item["reports"]]
		group = []
		for persona in compare["personas"]:
			row = {"번호": qid, "질문": item["question"], "페르소나": persona, "기대 특징": compare["expect"][persona]}
			try:
				row["답변"] = run_question(item["question"], report_ids, persona, k, use_mmr)["answer"]
			except Exception as error:
				row["답변"] = ""
				row["메모"] = f"실행 오류: {type(error).__name__}: {error}"
			row["글자 수"] = len(row["답변"])
			group.append(row)
			print(f"[페르소나] {qid} {persona} 완료")
		practitioner_len = next((r["글자 수"] for r in group if r["페르소나"] == "실무자" and r["글자 수"]), None)
		for row in group:
			row["형식 자동 후보"] = persona_format_check(row["페르소나"], row["답변"], practitioner_len)
		rows.extend(group)
	return rows


def read_csv(path: Path) -> list[dict]:
	"""엑셀에서 고쳐 저장한 CSV도 읽는다.

	'CSV UTF-8'로 저장하면 UTF-8(BOM), 한글 엑셀에서 'CSV (쉼표로 분리)'로 저장하면 CP949라 둘 다 시도한다.
	"""
	for encoding in ("utf-8-sig", "cp949"):
		try:
			with path.open(encoding=encoding, newline="") as f:
				return list(csv.DictReader(f))
		except UnicodeDecodeError:
			continue
	raise SystemExit(f"[중단] {path}의 인코딩을 읽지 못했습니다. 엑셀에서 'CSV UTF-8'로 다시 저장해 주세요.")


def main() -> None:
	parser = argparse.ArgumentParser(description="4.1.1 정답 질문 정확도·출처 일치율 평가")
	parser.add_argument("--answers", type=Path, default=DEFAULT_ANSWERS, help="정답셋 JSON (기본: tests/fixtures/qa_answers.json)")
	parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="결과 저장 폴더 (기본: logs/eval_qa)")
	parser.add_argument("--persona", default="실무자")
	parser.add_argument("--k", type=int, default=4)
	parser.add_argument("--mmr", action="store_true", help="use_mmr=True로 실행")
	parser.add_argument("--only", help="쉼표로 구분한 문항 번호. 예) Q01,Q02")
	parser.add_argument("--report-ids", help="report_id 직접 지정. 예) FX=3,PR=4,GR=5")
	parser.add_argument("--personas", action="store_true", help="페르소나 비교도 실행 (5문항 × 3 = 15회 추가 호출)")
	parser.add_argument("--dry-run", action="store_true", help="API를 부르지 않고 report_id·질문만 확인")
	parser.add_argument("--summary", type=Path, help="채점한 결과 CSV를 다시 집계만 한다 (API 호출 없음)")
	args = parser.parse_args()

	# 채점한 CSV 다시 집계: DB·API가 필요 없다
	if args.summary:
		write_summary(read_csv(args.summary), args.summary)
		return

	data = load_answers(args.answers)
	ids = resolve_report_ids(data, args.report_ids)
	only = set(args.only.split(",")) if args.only else None
	print(f"report_id: {ids}")

	if args.dry_run:
		for item in data["items"]:
			if not only or item["id"] in only:
				print(item["id"], [ids[c] for c in item["reports"]], item["question"])
		return

	from hri.config.settings import load_settings

	settings = load_settings()
	stamp = datetime.now().strftime("%Y%m%d_%H%M")
	conditions = (f"실행일 {datetime.now():%Y-%m-%d %H:%M} · persona={args.persona} · k={args.k} · "
		f"use_mmr={args.mmr} · THRESHOLD={settings.threshold} · 모델={settings.llm_model} · "
		f"report_id={ids} · 방식: 평가 스크립트(그래프 우회)")

	rows = run_accuracy(data, ids, only, args.persona, args.k, args.mmr)
	result_path = args.out_dir / f"qa_results_{stamp}.csv"
	write_csv(result_path, RESULT_COLUMNS, rows)
	print(f"\n결과 저장: {result_path}")

	if args.personas:
		persona_path = args.out_dir / f"persona_compare_{stamp}.csv"
		write_csv(persona_path, PERSONA_COLUMNS, run_personas(data, ids, args.k, args.mmr))
		print(f"페르소나 비교 저장: {persona_path}")

	write_summary(rows, result_path, conditions)
	print("\n정확도 점수·출처 일치·실패 원인은 자동 후보값입니다. CSV를 엑셀로 열어 답변을 보고 고친 뒤 저장하고,")
	print(f"poetry run python -m tests.eval_qa_accuracy --summary {result_path} 로 다시 집계하세요.")


if __name__ == "__main__":
	main()