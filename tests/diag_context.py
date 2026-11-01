"""근거 문맥 진단 — '자료에 없습니다'가 왜 나오는지 확인 (서동안)

정답 질문마다 실제 답변과 같은 방식으로 검색(hybrid_search → get_context_docs)한 뒤,
LLM에 넘어간 [자료]에 정답 키워드가 들어 있었는지 확인한다. LLM은 부르지 않는다 (질문 확장·임베딩 비용만).

키워드마다 다음 중 하나로 판정한다.
	[자료]에 있음          : LLM이 받은 문맥에 키워드가 있음 → 그런데도 '자료에 없습니다'면 프롬프트·모델 문제
	MAX_CONTEXT_CHARS 잘림 : 섹션 원문에는 있지만 앞 N자만 넣어서 빠짐 → 위치(몇 번째 글자)를 적는다
	검색 문서에 없음       : 가져온 섹션·표 어디에도 없음 → 검색이 정답 쪽을 못 가져왔거나 표 추출 문제

실행 (프로젝트 루트, .env·SSH 터널 필요)
	poetry run python -m tests.diag_context                       # 30문항
	poetry run python -m tests.diag_context --only Q02,Q24,Q25    # 일부만
	poetry run python -m tests.diag_context --show-context Q24    # 그 문항의 [자료] 전체도 리포트에 넣기
결과: logs/eval_qa/diag_context_<시각>.md
"""

import argparse
from datetime import datetime
from pathlib import Path

from tests.eval_qa_accuracy import DEFAULT_ANSWERS, _normalize, load_answers, pages_text, resolve_report_ids
from tests.eval_report import PASS, PARTIAL, details, table, write_report

OUT_DIR = Path("logs/eval_qa")


def find_keyword(text: str, keyword: str) -> int:
	"""정규화(공백·쉼표·%·△·마이너스 제거)한 기준으로 키워드 위치를 찾아 원문 글자 위치로 돌려준다. 없으면 -1."""
	target = _normalize(keyword)
	if not target:
		return -1
	# 원문 글자마다 정규화 후 글자를 대응시켜 위치를 되찾는다
	kept, index = [], []
	for i, ch in enumerate(text):
		if _normalize(ch):
			kept.append(ch)
			index.append(i)
	pos = "".join(kept).find(target)
	return index[pos] if pos >= 0 else -1


def full_context_docs(chunks):
	"""MAX_CONTEXT_CHARS를 끈 get_context_docs() — 잘리기 전 섹션 원문을 본다."""
	import hri.dao.report_dao as report_dao

	original = report_dao.load_settings
	report_dao.load_settings = lambda: original().model_copy(update={"max_context_chars": None})
	try:
		return report_dao.get_context_docs(chunks)
	finally:
		report_dao.load_settings = original


def diagnose(item: dict, ids: dict[str, int], k: int, use_mmr: bool, limit) -> dict:
	from hri.dao.report_dao import get_context_docs
	from hri.retriever.search import hybrid_search

	code_of = {v: c for c, v in ids.items()}
	chunks = hybrid_search(item["question"], k=k, report_ids=[ids[c] for c in item["reports"]], use_mmr=use_mmr)
	used = get_context_docs(chunks)
	full = full_context_docs(chunks)
	used_text = "\n".join(d.text for d in used)

	kw_rows, verdicts = [], []
	for kw in item["keywords"]:
		if find_keyword(used_text, kw) >= 0:
			verdict, where = "[자료]에 있음", next(f"{code_of.get(d.report_id)} p.{d.page_start}-{d.page_end} ({d.kind})"
				for d in used if find_keyword(d.text, kw) >= 0)
		else:
			hit = next(((d, find_keyword(d.text, kw)) for d in full if find_keyword(d.text, kw) >= 0), None)
			if hit:
				d, pos = hit
				verdict = "MAX_CONTEXT_CHARS 잘림"
				where = (f"{code_of.get(d.report_id)} p.{d.page_start}-{d.page_end} 섹션 {pos + 1:,}번째 글자 "
					f"(섹션 {len(d.text):,}자 중 앞 {limit:,}자만 넣음)")
			else:
				verdict, where = "검색 문서에 없음", "-"
		verdicts.append(verdict)
		kw_rows.append([kw, verdict, where])

	if all(v == "[자료]에 있음" for v in verdicts):
		cause = "키워드가 모두 [자료]에 있음 → 거절했다면 프롬프트·모델 문제"
	elif any(v == "MAX_CONTEXT_CHARS 잘림" for v in verdicts):
		cause = "MAX_CONTEXT_CHARS 잘림"
	else:
		cause = "검색 문서에 없음 (검색·표 추출)"
	docs_rows = [[f"{code_of.get(d.report_id)} p.{d.page_start}-{d.page_end}", d.kind, d.section_title or d.caption or "",
		f"{len(f.text):,}", f"{len(d.text):,}", PARTIAL + " 잘림" if len(f.text) > len(d.text) else PASS]
		for d, f in zip(used, full)]
	return {"id": item["id"], "question": item["question"], "answer": item["answer"], "pages": pages_text(item["pages"]),
		"cause": cause, "kw_rows": kw_rows, "docs_rows": docs_rows, "used_text": used_text,
		"used_chars": len(used_text), "full_chars": sum(len(d.text) for d in full)}


def main() -> None:
	parser = argparse.ArgumentParser(description="근거 문맥 진단 (정답 키워드가 [자료]에 들어갔는지)")
	parser.add_argument("--only", help="쉼표로 구분한 문항 번호")
	parser.add_argument("--report-ids", help="예) FX=3,PR=4,GR=5")
	parser.add_argument("--k", type=int, default=4)
	parser.add_argument("--mmr", action="store_true")
	parser.add_argument("--show-context", default="", help="[자료] 전문을 리포트에 넣을 문항 번호 (쉼표 구분)")
	args = parser.parse_args()

	from hri.config.settings import load_settings

	settings = load_settings()
	limit = settings.max_context_chars
	data = load_answers(DEFAULT_ANSWERS)
	ids = resolve_report_ids(data, args.report_ids)
	only = set(args.only.split(",")) if args.only else None
	show = set(filter(None, args.show_context.split(",")))
	items = [i for i in data["items"] if not only or i["id"] in only]

	results = []
	for n, item in enumerate(items, 1):
		r = diagnose(item, ids, args.k, args.mmr, limit or 0)
		results.append(r)
		print(f"[{n:>2}/{len(items)}] {r['id']} {r['cause']}")

	causes = {}
	for r in results:
		causes.setdefault(r["cause"], []).append(r["id"])
	lines = ["# 근거 문맥 진단", "",
		f"실행일 {datetime.now():%Y-%m-%d %H:%M} · k={args.k} · use_mmr={args.mmr} · THRESHOLD={settings.threshold} · "
		f"MAX_CONTEXT_CHARS={limit} · report_id={ids}", "",
		"## 0. 진단 방식", "",
		"답변과 같은 방식으로 검색해 LLM에 넘어가는 [자료]를 만들고, 정답셋의 채점 키워드가 그 안에 있는지 본다 (LLM은 부르지 않음).",
		"없으면 MAX_CONTEXT_CHARS를 끈 섹션 원문에서 다시 찾아, 잘려서 빠진 것인지 처음부터 없던 것인지 나눈다.", "",
		"## 1. 원인별 문항", ""]
	lines += table(["원인", "문항 수", "문항"], [[c, len(v), ", ".join(v)] for c, v in causes.items()])
	lines += ["## 2. 문항별", ""]
	for r in results:
		lines += [f"### {r['id']} — {r['cause']}", "", f"**질문**: {r['question']}", "",
			f"**기대 답변**: {r['answer']} (정답 쪽: {r['pages']})", "",
			f"**LLM에 넘어간 [자료]**: {r['used_chars']:,}자 (잘리기 전 {r['full_chars']:,}자)", ""]
		lines += table(["근거 문서", "종류", "섹션·캡션", "원문 글자 수", "넣은 글자 수", "잘림"], r["docs_rows"])
		lines += table(["채점 키워드", "판정", "위치"], r["kw_rows"])
		if r["id"] in show:
			lines += details("[자료] 전문 (펼치기)", ["```text", r["used_text"], "```"])
	path = write_report(OUT_DIR / f"diag_context_{datetime.now():%Y%m%d_%H%M}.md", lines)
	print(f"\n리포트 저장: {path}")


if __name__ == "__main__":
	main()
