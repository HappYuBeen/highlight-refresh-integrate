"""4.1.4 요약 품질·JSON 형식 점검 스크립트 (서동안)

보고서 PDF를 업로드와 같은 방식(load_pdf → clean_text → find_body_start → split_sections)으로 섹션으로 나눈 뒤,
summarize_sections()·summarize_three_lines()를 실제 LLM으로 실행해 품질과 형식을 자동 점검한다.
DB·Chroma 없이 PDF만 있으면 된다 (--report-id를 주면 DB의 섹션을 쓴다).

자동 점검 (prompt_design.md 11장)
	① 3줄: 정확히 3줄 / 각 줄 60자 이내 / 줄 앞 번호·기호 없음 / 2줄(근거 수치)에 숫자가 있음
	② 3줄의 숫자가 모두 섹션 요약에 있음 (없으면 지어낸 수치 후보)
	③ 섹션 요약: 2~3문장 / 제목·페이지 범위가 원래 섹션과 같음 / 요약의 숫자가 모두 섹션 원문에 있음
	③-2 reasoning이 주장 → 수치 → 시사점 순서인지는 사람이 본다 (CSV에 reasoning을 함께 저장)
	④ 형식 재시도: 섹션·3줄마다 LLM 호출 횟수를 센다 (1번 = 한 번에 통과, 2번 = 재시도, 실패 = OutputFormatError)
	⑥ 병합(--merge): 결론 있음·3문장 이내 / 공통·차이 / 공통 의견 출처 표시 / 요약에 없는 숫자 없음, 시간·토큰·비용

이 스크립트가 다루지 않는 4.1.4 항목
	- 형식을 일부러 어겼을 때 재시도·OutputFormatError, 요약·병합 화면 문구: tests/test_summary_format.py (LLM 없이)
	- 요약 캐시 로직(저장 → 읽기 왕복, 깨진 캐시 다시 만들기): tests/test_summary_cache.py (DB·LLM 없이)

요약 캐시 실제 확인(--cache-check, ⑤)
	DB에 있는 보고서로 summarize_report()를 두 번 부른다. 두 번째 호출은 LLM 토큰이 0이고 결과가 첫 번째와 같아야 한다.
	앱에서 요약 버튼을 두 번 누른 것과 같아서, 첫 호출에 캐시가 없으면 summary_json 저장과 llm_usage 기록이 실제로 남는다.
	(위 ①~④ 점검은 요약 함수를 직접 부르므로 캐시와 상관없이 매번 새로 요약한다)

실행 (프로젝트 루트에서. OpenAI API 사용, 보고서 1개당 LLM 약 5~7회)
	poetry run python -m tests.eval_summary --pdf data/raw/환율.pdf data/raw/물가.pdf data/raw/수정전망.pdf
	poetry run python -m tests.eval_summary --pdf data/raw/환율.pdf --sections-only    # 섹션 나누기만 확인 (API 없음)
	poetry run python -m tests.eval_summary --report-id 3                              # DB에 저장된 섹션 사용
	poetry run python -m tests.eval_summary --cache-check 3                            # 요약 캐시 실제 확인 (DB 필요)
	poetry run python -m tests.eval_summary --merge 3,4 3,4,5                          # 병합 결과 점검 (DB 필요)

결과는 logs/eval_summary/에 저장한다 (logs/는 .gitignore 대상).
	summary_<시각>_리포트.md       : 섹션·3줄 요약마다 입력(원문·섹션 요약) → 기대 결과 → 실제 출력 → 항목별 기대/실제/판정 → 판정 계산
	summary_merge_<시각>_리포트.md : 병합 결과 상세 (--merge)
	summary_cache_<시각>_리포트.md : 캐시 확인 상세 (--cache-check)
"""

import argparse
import csv
import re
from datetime import datetime
from pathlib import Path

from tests.eval_report import FAIL, checks_table, details, mark, quote, table, write_report

DEFAULT_OUT_DIR = Path("logs/eval_summary")
LINE_MAX_CHARS = 60  # summary_prompt.LINE_MAX_CHARS와 같은 값

SECTION_COLUMNS = ["보고서", "section_no", "제목", "쪽", "원문 글자 수", "요약", "문장 수", "2~3문장",
	"제목·쪽 일치", "원문에 없는 숫자", "LLM 호출 수", "판정(자동)", "사람 확인(O/X)", "메모"]
THREE_COLUMNS = ["보고서", "1줄(주장)", "2줄(근거 수치)", "3줄(시사점)", "줄 길이", "reasoning", "형식 위반",
	"섹션 요약에 없는 숫자", "LLM 호출 수", "판정(자동)", "주장→수치→시사점 순서(O/X)", "reasoning 순서(O/X)", "메모"]


# ---------------------------------------------------------------------
# 1. 섹션 준비
# ---------------------------------------------------------------------
def sections_from_pdf(path: Path):
	"""업로드(ingest_graph)와 같은 순서로 PDF를 섹션으로 나눈다."""
	from hri.loader.pdf_loader import clean_text, find_body_start, load_pdf
	from hri.splitter.text_splitter import split_sections

	return split_sections(find_body_start(clean_text(load_pdf(str(path)))))


def sections_from_db(report_id: int):
	from hri.dao.report_dao import select_sections

	return select_sections(report_id)


# ---------------------------------------------------------------------
# 2. LLM 호출 횟수 세기 (재시도 확인용)
# ---------------------------------------------------------------------
class CallCounter:
	"""summary_prompt.get_chat_model을 감싸서 구조화 출력 호출(invoke) 횟수를 센다."""

	def __init__(self):
		self.count = 0

	def install(self):
		import hri.prompt.summary_prompt as summary_prompt

		original = summary_prompt.get_chat_model
		counter = self

		def counted_get_chat_model(**kwargs):
			model = original(**kwargs)

			class _Counted:
				def with_structured_output(self, schema, **kw):
					runnable = model.with_structured_output(schema, **kw)

					class _Invoke:
						def invoke(self, messages, *args, **kws):
							counter.count += 1
							return runnable.invoke(messages, *args, **kws)

					return _Invoke()

			return _Counted()

		summary_prompt.get_chat_model = counted_get_chat_model

	def take(self) -> int:
		value, self.count = self.count, 0
		return value


# ---------------------------------------------------------------------
# 3. 자동 점검
# ---------------------------------------------------------------------
def numbers(text: str) -> set[str]:
	"""문장 속 숫자. 쉼표를 지우고, '00.1 같은 연.월 표기는 연·월로 나눈다. 1자리 정수(1~9)는 서수·개수가 많아 뺀다."""
	text = re.sub(r"'(\d{2})\.(\d{1,2})", r"'\1년 \2월", (text or "").replace(",", ""))
	return {n for n in re.findall(r"\d+(?:\.\d+)?", text) if not re.fullmatch(r"\d", n)}


def sentence_count(text: str) -> int:
	"""'다.'·'.'·'?'로 끝나는 문장 수. 소수점(0.5)은 문장 끝으로 보지 않는다."""
	return len(re.findall(r"(?<!\d)[.?!](?!\d)", text.strip())) or (1 if text.strip() else 0)


def check_section(section, summary, calls: int) -> dict:
	sents = sentence_count(summary.summary)
	same_meta = (summary.title == section.title and summary.start_page == section.start_page
		and summary.end_page == section.end_page)
	summary_numbers = numbers(summary.summary)
	missing = sorted(summary_numbers - numbers(section.text))
	checks = [
		_check("문장 수", "2~3문장", f"{sents}문장", 2 <= sents <= 3, "'.'·'?'·'!' 개수, 소수점 제외"),
		_check("제목", section.title, summary.title, summary.title == section.title),
		_check("쪽 범위", f"p.{section.start_page}-{section.end_page}", f"p.{summary.start_page}-{summary.end_page}",
			summary.start_page == section.start_page and summary.end_page == section.end_page),
		_check("숫자가 원문에 있음", "요약의 숫자가 모두 원문에 있음",
			f"요약 숫자 {_numbers_text(summary_numbers)} / 원문에 없는 것 {_numbers_text(missing) if missing else '없음'}", not missing),
		_check("LLM 호출 수", "1회 (한 번에 형식 통과)", f"{calls}회", None, "2회 = 형식 재시도"),
	]
	problems = []
	if not 2 <= sents <= 3:
		problems.append(f"{sents}문장")
	if not same_meta:
		problems.append("제목·쪽 불일치")
	if missing:
		problems.append("원문에 없는 숫자")
	return {
		"section_no": section.section_no, "제목": section.title, "쪽": f"p.{section.start_page}-{section.end_page}",
		"원문 글자 수": len(section.text), "요약": summary.summary, "문장 수": sents,
		"2~3문장": "O" if 2 <= sents <= 3 else "X", "제목·쪽 일치": "O" if same_meta else "X",
		"원문에 없는 숫자": ", ".join(missing), "LLM 호출 수": calls,
		"판정(자동)": "통과" if not problems else "확인 필요: " + ", ".join(problems),
		"_checks": checks, "_source": section.text,  # 리포트용 (CSV에는 안 들어감)
	}


def check_three_lines(result, section_summaries, calls: int) -> dict:
	lines = result.lines
	problems = []
	if len(lines) != 3:
		problems.append(f"{len(lines)}줄")
	long_lines = [i + 1 for i, line in enumerate(lines) if len(line) > LINE_MAX_CHARS]
	if long_lines:
		problems.append(f"{LINE_MAX_CHARS}자 초과({', '.join(map(str, long_lines))}줄)")
	if any(re.match(r"^\s*(?:\d+[.)]|[-•·*①-⑩])", line) for line in lines):
		problems.append("번호·기호로 시작")
	if len(lines) >= 2 and not re.search(r"\d", lines[1]):
		problems.append("2줄에 숫자 없음")
	source = " ".join(s.summary for s in section_summaries)
	line_numbers = numbers(" ".join(lines))
	missing = sorted(line_numbers - numbers(source))
	if missing:
		problems.append("섹션 요약에 없는 숫자")
	starts = [i + 1 for i, line in enumerate(lines) if re.match(r"^\s*(?:\d+[.)]|[-•·*①-⑩])", line)]
	checks = [
		_check("줄 수", "3줄", f"{len(lines)}줄", len(lines) == 3),
		_check("줄 길이", f"줄마다 {LINE_MAX_CHARS}자 이내", " / ".join(f"{i + 1}줄 {len(line)}자" for i, line in enumerate(lines)),
			not long_lines, f"초과: {', '.join(f'{n}줄' for n in long_lines)}" if long_lines else None),
		_check("줄 앞 번호·기호", "없음", ", ".join(f"{n}줄" for n in starts) or "없음", not starts),
		_check("2줄(근거 수치)에 숫자", "있음", _numbers_text(numbers(lines[1])) if len(lines) >= 2 else "(2줄 없음)",
			len(lines) >= 2 and bool(re.search(r"\d", lines[1]))),
		_check("숫자가 섹션 요약에 있음", "3줄의 숫자가 모두 섹션 요약에 있음",
			f"3줄 숫자 {_numbers_text(line_numbers)} / 섹션 요약에 없는 것 {_numbers_text(missing) if missing else '없음'}", not missing),
		_check("LLM 호출 수", "1회 (한 번에 형식 통과)", f"{calls}회", None, "2회 = 형식 재시도"),
	]
	padded = (lines + ["", "", ""])[:3]
	return {
		"1줄(주장)": padded[0], "2줄(근거 수치)": padded[1], "3줄(시사점)": padded[2],
		"줄 길이": " / ".join(str(len(line)) for line in lines), "reasoning": result.reasoning,
		"형식 위반": ", ".join(p for p in problems if p != "섹션 요약에 없는 숫자"),
		"섹션 요약에 없는 숫자": ", ".join(missing), "LLM 호출 수": calls,
		"판정(자동)": "통과" if not problems else "확인 필요: " + ", ".join(problems),
		"_checks": checks, "_inputs": [f"[{s.title}] {s.summary}" for s in section_summaries],
	}


# ---------------------------------------------------------------------
# 4-1. 마크다운 리포트 (입력 → 기대 → 실제 출력 → 평가 근거 → 판정 계산)
# ---------------------------------------------------------------------
SOURCE_PREVIEW_CHARS = 1500  # 리포트에 펼쳐 볼 원문 길이 (너무 길면 파일이 커짐)


def _check(item: str, expected, actual, ok, note: str | None = None) -> dict:
	"""평가 근거 표 한 줄. ok=None이면 참고용(판정에 안 씀)."""
	return {"항목": item, "기대": expected, "실제": actual if note is None else f"{actual} ({note})", "판정": mark(ok) if ok is not None else "참고"}


def _numbers_text(values) -> str:
	values = sorted(values, key=lambda v: (len(v), v))
	return ", ".join(values) if values else "(숫자 없음)"


def _verdict_calc(row: dict) -> str:
	checks = [c for c in row.get("_checks", []) if c["판정"] != "참고"]
	bad = [c["항목"] for c in checks if c["판정"] == FAIL]
	if not checks:
		return f"자동 점검을 하지 못함 → {row.get('판정(자동)')}"
	if not bad:
		return f"판정 항목 {len(checks)}개 모두 통과 → **통과**"
	return f"판정 항목 {len(checks)}개 중 {len(bad)}개 실패({', '.join(bad)}) → **확인 필요**"


def section_markdown(row: dict) -> list[str]:
	ok = row.get("판정(자동)") == "통과"
	lines = [f"#### 섹션 {row.get('section_no')} · {row.get('제목')} ({row.get('쪽')}) · {mark(ok)} {row.get('판정(자동)')}", ""]
	source = row.get("_source") or ""
	preview = source[:SOURCE_PREVIEW_CHARS] + (f"\n… (이하 {len(source) - SOURCE_PREVIEW_CHARS:,}자 생략)" if len(source) > SOURCE_PREVIEW_CHARS else "")
	lines += details(f"입력: 섹션 원문 {row.get('원문 글자 수', len(source)):,}자 (펼치기)", ["```text", preview, "```"])
	lines += ["**기대 결과**: 원문 내용을 2~3문장으로 요약 · 제목과 쪽 범위는 원래 섹션 그대로 · 원문에 있는 숫자만 사용", "",
		"**실제 출력**", "", *quote(row.get("요약"), "요약 없음 (형식 실패)"), "**평가 근거**", ""]
	lines += checks_table(row["_checks"]) if row.get("_checks") else ["(형식 실패라 점검할 출력이 없음)", ""]
	lines += [f"**판정 계산**: {_verdict_calc(row)}", "", "---", ""]
	return lines


def three_markdown(row: dict) -> list[str]:
	ok = row.get("판정(자동)") == "통과"
	lines = [f"### {row.get('보고서')} 3줄 요약 · {mark(ok)} {row.get('판정(자동)')}", ""]
	inputs = row.get("_inputs") or []
	if inputs:
		lines += details(f"입력: 섹션 요약 {len(inputs)}개 (펼치기)", [f"{i}. {text}" for i, text in enumerate(inputs, 1)] + [""])
	lines += ["**기대 결과**: 정확히 3줄 · 1줄 핵심 주장 / 2줄 근거 수치 / 3줄 시사점 · 줄마다 "
		f"{LINE_MAX_CHARS}자 이내 · 번호·기호 없이 시작 · 숫자는 섹션 요약에 있는 것만", "", "**실제 출력**", ""]
	three = [row.get(k) for k in ("1줄(주장)", "2줄(근거 수치)", "3줄(시사점)") if row.get(k)]
	lines += quote("\n".join(f"{i}. {t} ({len(t)}자)" for i, t in enumerate(three, 1)), "3줄 요약 없음 (형식 실패)")
	if row.get("reasoning"):
		lines += ["reasoning (내부 생각 과정, 사람이 주장→수치→시사점 순서인지 확인):", "", *quote(row["reasoning"])]
	lines += ["**평가 근거**", ""]
	lines += checks_table(row["_checks"]) if row.get("_checks") else ["(형식 실패라 점검할 출력이 없음)", ""]
	lines += [f"**판정 계산**: {_verdict_calc(row)}", "", "---", ""]
	return lines


def build_report(section_rows: list[dict], three_rows: list[dict], conditions: str) -> list[str]:
	"""리포트 전체: 평가 방식 → 요약(계산식 포함) → 보고서별 3줄 요약 상세 → 섹션 요약 상세."""
	def ratio(rows, pred) -> str:
		hit = sum(1 for r in rows if pred(r))
		return f"{hit} / {len(rows)} = **{hit / len(rows):.1%}**" if rows else "-"

	scored = [r for r in section_rows if r.get("_checks")]
	lines = ["# 4.1.4 요약 품질·JSON 형식 점검 리포트", "", conditions, "",
		"## 0. 평가 방식", "",
		"- **섹션 요약**: 섹션마다 summarize_sections()를 실제 LLM으로 부르고, ① 2~3문장 ② 제목·쪽 범위가 원래 섹션과 같음 "
		"③ 요약의 숫자가 모두 원문에 있음(없으면 지어낸 수치 후보)을 본다. LLM 호출 수(1번=한 번에 통과, 2번=형식 재시도)는 참고값.",
		f"- **3줄 요약**: ① 정확히 3줄 ② 줄마다 {LINE_MAX_CHARS}자 이내 ③ 번호·기호로 시작하지 않음 ④ 2줄(근거 수치)에 숫자가 있음 "
		"⑤ 3줄의 숫자가 모두 섹션 요약에 있음.",
		"- 숫자 비교: 쉼표를 지우고 '00.1 같은 연.월 표기는 연·월로 나눈 뒤, 1자리 정수(1~9)는 서수·개수가 많아 뺀다.",
		"- 문장 수: '.'·'?'·'!' 개수 (소수점은 제외).",
		"- 모든 판정 항목이 통과면 '통과', 하나라도 실패면 '확인 필요'. 원문에 없는 숫자는 단위 환산·반올림일 수 있으니 사람이 확인한다.", "",
		"## 1. 결과 요약", "",
		f"- 섹션 자동 점검 통과율 = {ratio(section_rows, lambda r: r.get('판정(자동)') == '통과')}",
		f"- 2~3문장 = {ratio(scored, lambda r: r.get('2~3문장') == 'O')}",
		f"- 제목·쪽 일치 = {ratio(scored, lambda r: r.get('제목·쪽 일치') == 'O')}",
		f"- 원문에 없는 숫자 없음 = {ratio(scored, lambda r: not r.get('원문에 없는 숫자'))}",
		f"- 한 번에 형식 통과(LLM 1회) = {ratio(section_rows, lambda r: (r.get('LLM 호출 수') or 0) == 1)}",
		f"- 형식 실패(OutputFormatError) = {ratio(section_rows, lambda r: str(r.get('판정(자동)', '')).startswith('형식 실패'))}",
		f"- 3줄 요약 자동 점검 통과율 = {ratio(three_rows, lambda r: r.get('판정(자동)') == '통과')}", ""]

	lines += ["## 2. 보고서별 한눈에 보기", ""]
	overview = []
	for name in dict.fromkeys(r.get("보고서") for r in section_rows):
		group = [r for r in section_rows if r.get("보고서") == name]
		three = next((t for t in three_rows if t.get("보고서") == name), {})
		fails = [str(r.get("section_no")) for r in group if r.get("판정(자동)") != "통과"]
		overview.append([name, len(group), sum(r.get("판정(자동)") == "통과" for r in group), ", ".join(fails) or "없음",
			f"{mark(three.get('판정(자동)') == '통과')} {three.get('판정(자동)', '-')}"])
	lines += table(["보고서", "섹션 수", "섹션 통과", "확인 필요한 섹션", "3줄 요약"], overview)

	for name in dict.fromkeys(r.get("보고서") for r in section_rows):
		lines += [f"## 3. 상세 — {name}", ""]
		three = next((t for t in three_rows if t.get("보고서") == name), None)
		if three:
			lines += three_markdown(three)
		lines += [f"### {name} 섹션 요약", ""]
		for r in (r for r in section_rows if r.get("보고서") == name):
			lines += section_markdown(r)
	lines += ["## 사람이 확인할 것", "",
		"- 3줄 요약: 1줄이 핵심 주장, 2줄이 근거 수치, 3줄이 시사점인지 / reasoning이 같은 순서인지 (CSV O/X 칸)",
		"- 섹션 요약: '원문에 없는 숫자'가 실제로 지어낸 수치인지 (단위 환산·반올림이면 문제없음)",
		"- 섹션 제목이 이상한 것(각주·차트 주석이 제목으로 잡힘)은 요약 문제가 아니라 섹션 나누기(split_sections) 문제"]
	return lines


def merge_report(rows: list[dict], conditions: str) -> list[str]:
	lines = ["# 4.1.4 병합 결과 점검 리포트 (⑥)", "", conditions, "",
		"## 0. 평가 방식", "",
		"merge_reports()를 실제로 실행하고 ① 결론이 있음 ② 결론 3문장 이내 ③ 공통·차이 중 하나 이상 "
		"④ 공통 의견마다 보고서 출처 괄호 표시 ⑤ 병합 결과의 숫자가 모두 각 보고서 요약(섹션 요약·3줄)에 있음을 본다. "
		"모두 통과면 '통과'.", ""]
	lines += table(["report_ids", "공통", "차이", "시간(초)", "토큰", "비용(원)", "판정"],
		[[r["report_ids"], r["공통 수"], r["차이 수"], r["시간(초)"], r["토큰"], r["비용(원)"], f"{mark(r['판정(자동)'] == '통과')} {r['판정(자동)']}"] for r in rows])
	for r in rows:
		lines += [f"## report_id {r['report_ids']} 병합 · {mark(r['판정(자동)'] == '통과')} {r['판정(자동)']}", "",
			"**입력**: 각 보고서의 요약 (캐시가 있으면 재사용)", "",
			"**기대 결과**: 공통 의견(출처 표시) · 차이점 · 3문장 이내 종합 결론, 요약에 없는 숫자 없음", "",
			"**실제 출력**", "", "공통 의견:", ""]
		lines += [f"- {c}" for c in r.get("_common", [])] or ["- (없음)"]
		lines += ["", "차이점:", ""] + ([f"- {d}" for d in r.get("_differences", [])] or ["- (없음)"])
		lines += ["", "결론:", "", *quote(r.get("결론")), "**평가 근거**", ""]
		lines += checks_table(r.get("_checks", []))
		lines += [f"**판정 계산**: {_verdict_calc(r)}", "", "---", ""]
	return lines


def cache_report(rows: list[dict]) -> list[str]:
	lines = ["# 4.1.4 요약 캐시 확인 리포트 (⑤)", "",
		"## 0. 평가 방식", "",
		"summarize_report()를 같은 report_id로 두 번 부른다. 두 번째는 저장된 summary_json을 읽어야 하므로 "
		"① 2회 LLM 토큰 = 0 ② 1회와 2회 결과(3줄·섹션 요약)가 같음 ③ 처음에 캐시가 없었다면 1회 토큰 > 0(실제로 요약을 만들어 저장)이어야 통과.", ""]
	lines += table(["report_id", "처음부터 캐시", "1회 토큰·시간", "2회 토큰·시간", "결과 같음", "판정"],
		[[r["report_id"], "있음" if r["처음부터 캐시 있음"] else "없음", f"{r['1회 토큰']:,} · {r['1회 시간(초)']}초",
			f"{r['2회 토큰']:,} · {r['2회 시간(초)']}초", mark(r["결과 같음"]), r["판정"]] for r in rows])
	for r in rows:
		checks = [
			_check("2회 LLM 토큰", "0 (캐시에서 읽음)", f"{r['2회 토큰']:,}", r["2회 토큰"] == 0),
			_check("결과 같음", "1회 = 2회", "같음" if r["결과 같음"] else "다름", r["결과 같음"]),
			_check("1회 동작", "캐시가 없었다면 토큰 > 0", f"처음부터 캐시 {'있음' if r['처음부터 캐시 있음'] else '없음'} · 1회 토큰 {r['1회 토큰']:,}",
				r["처음부터 캐시 있음"] or r["1회 토큰"] > 0),
			_check("응답 시간", "2회가 더 짧음", f"{r['1회 시간(초)']}초 → {r['2회 시간(초)']}초", None),
		]
		lines += [f"## report_id {r['report_id']} · {mark(r['판정'] == '통과')} {r['판정']}", ""] + checks_table(checks)
		lines += [f"**판정 계산**: {_verdict_calc({'_checks': checks})}", ""]
	return lines


# ---------------------------------------------------------------------
# 4. 실행
# ---------------------------------------------------------------------
def run_report(name: str, sections, counter: CallCounter) -> tuple[list[dict], dict]:
	"""보고서 1개: 섹션마다 요약하고 3줄 요약을 만든다. 형식 실패(OutputFormatError)도 결과로 남긴다."""
	from hri.exception.errors import OutputFormatError
	from hri.graph.summary_graph import sort_section_summaries
	from hri.prompt.summary_prompt import summarize_sections, summarize_three_lines

	section_rows, summaries = [], []
	for section in sections:
		try:
			summary = summarize_sections([section])[0]  # summary_graph의 summarize_section 노드와 같이 1개씩
			row = check_section(section, summary, counter.take())
			summaries.append(summary)
		except OutputFormatError as error:
			row = {"section_no": section.section_no, "제목": section.title,
				"쪽": f"p.{section.start_page}-{section.end_page}", "원문 글자 수": len(section.text),
				"LLM 호출 수": counter.take(), "판정(자동)": f"형식 실패(OutputFormatError): {error}", "_source": section.text}
		row["보고서"] = name
		section_rows.append(row)
		print(f"  섹션 {section.section_no} {section.title[:20]:<20} 호출 {row['LLM 호출 수']}회 · {row['판정(자동)']}")

	if not summaries:
		return section_rows, {"보고서": name, "판정(자동)": "섹션 요약이 없어 3줄 요약 불가"}
	try:
		ordered = sort_section_summaries(summaries)
		result = summarize_three_lines(ordered)
		three = check_three_lines(result, ordered, counter.take())
	except OutputFormatError as error:
		three = {"LLM 호출 수": counter.take(), "판정(자동)": f"형식 실패(OutputFormatError): {error}"}
	three["보고서"] = name
	print(f"  3줄 요약 호출 {three['LLM 호출 수']}회 · {three['판정(자동)']}")
	return section_rows, three


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	with path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM: 엑셀에서 한글이 깨지지 않게
		writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
		writer.writeheader()
		writer.writerows(rows)


def summarize_results(section_rows: list[dict], three_rows: list[dict], conditions: str) -> str:
	def rate(rows, key, ok="O"):
		values = [r.get(key) for r in rows if r.get(key) in ("O", "X")]
		return f"{sum(v == ok for v in values)}/{len(values)}" if values else "-"

	sec_ok = [r for r in section_rows if r.get("판정(자동)") == "통과"]
	sec_retry = [r for r in section_rows if (r.get("LLM 호출 수") or 0) > 1]
	sec_fail = [r for r in section_rows if str(r.get("판정(자동)", "")).startswith("형식 실패")]
	three_ok = [r for r in three_rows if r.get("판정(자동)") == "통과"]

	lines = ["# 4.1.4 요약 품질·JSON 형식 점검 결과", "", conditions, "",
		"## 섹션 요약", "", "| 항목 | 결과 |", "|---|---|",
		f"| 섹션 수 | {len(section_rows)} |",
		f"| 자동 점검 통과 | {len(sec_ok)}/{len(section_rows)} |",
		f"| 2~3문장 | {rate(section_rows, '2~3문장')} |",
		f"| 제목·쪽 범위가 원래 섹션과 같음 | {rate(section_rows, '제목·쪽 일치')} |",
		f"| 원문에 없는 숫자가 있는 섹션 | {sum(bool(r.get('원문에 없는 숫자')) for r in section_rows)} |",
		f"| 재시도(LLM 2회 이상) | {len(sec_retry)} |",
		f"| 형식 실패(OutputFormatError) | {len(sec_fail)} |", "",
		"## 3줄 요약", "", "| 보고서 | 줄 길이 | 형식 위반 | 섹션 요약에 없는 숫자 | 호출 | 판정 |", "|---|---|---|---|---|---|"]
	for r in three_rows:
		lines.append(f"| {r.get('보고서')} | {r.get('줄 길이', '')} | {r.get('형식 위반', '') or '-'} | "
			f"{r.get('섹션 요약에 없는 숫자', '') or '-'} | {r.get('LLM 호출 수', '')} | {r.get('판정(자동)')} |")
	lines += ["", f"3줄 요약 자동 점검 통과: {len(three_ok)}/{len(three_rows)}", "",
		"## 사람이 확인할 것", "",
		"- 3줄 CSV: 1줄이 핵심 주장, 2줄이 근거 수치, 3줄이 시사점인지 / reasoning이 같은 순서인지 (O/X 칸)",
		"- 섹션 CSV: '원문에 없는 숫자'가 실제로 지어낸 수치인지 (단위 환산·반올림이면 문제없음)",
		"- 섹션 제목이 이상한 것(각주·차트 주석이 제목으로 잡힘)은 요약 문제가 아니라 섹션 나누기(split_sections) 문제"]
	return "\n".join(lines) + "\n"


def cache_check(report_id: int) -> dict:
	"""summarize_report()를 두 번 불러 두 번째가 캐시에서 나오는지(LLM 토큰 0, 결과 같음) 확인한다."""
	import time

	from langchain_core.callbacks import get_usage_metadata_callback

	from hri.dao.report_dao import select_summary_json
	from hri.service.summary_service import summarize_report

	had_cache = select_summary_json(report_id) is not None
	runs = []
	for _ in range(2):
		started = time.perf_counter()
		with get_usage_metadata_callback() as usage_cb:
			result = summarize_report(report_id)
		tokens = sum(u.get("total_tokens", 0) for u in usage_cb.usage_metadata.values())
		runs.append({"result": result, "tokens": tokens, "sec": round(time.perf_counter() - started, 2)})

	first, second = runs
	same = (first["result"].three_lines == second["result"].three_lines
		and [s.model_dump() for s in first["result"].sections] == [s.model_dump() for s in second["result"].sections])
	ok = second["tokens"] == 0 and same and (had_cache or first["tokens"] > 0)
	return {
		"report_id": report_id, "처음부터 캐시 있음": had_cache,
		"1회 토큰": first["tokens"], "1회 시간(초)": first["sec"],
		"2회 토큰": second["tokens"], "2회 시간(초)": second["sec"],
		"결과 같음": same, "판정": "통과" if ok else "확인 필요",
	}


def merge_check(report_ids: list[int]) -> dict:
	"""merge_reports()를 실제로 실행해 병합 결과 형식·내용을 점검한다 (요약은 캐시가 있으면 재사용).

	자동 점검: 결론이 있음 / 결론 3문장 이내 / 공통 또는 차이가 있음 / 공통 의견에 보고서 출처 표시 /
	병합 결과의 숫자가 모두 보고서들의 섹션 요약에 있음(없으면 지어낸 수치 후보)
	"""
	import time

	from langchain_core.callbacks import get_usage_metadata_callback

	from hri.llm.llm_client import cost_korean_won, get_usd_krw
	from hri.prompt.summary_prompt import merge_reports
	from hri.service.summary_service import summarize_report

	started = time.perf_counter()
	with get_usage_metadata_callback() as usage_cb:
		summaries = [summarize_report(i) for i in report_ids]
		merged = merge_reports(report_ids)
	usage = dict(usage_cb.usage_metadata)
	source_numbers = set()
	for summary in summaries:
		for section in summary.sections:
			source_numbers |= numbers(section.summary)
		for line in summary.three_lines:
			source_numbers |= numbers(line)
	result_text = " ".join(merged.common + merged.differences + [merged.conclusion])
	invented = sorted(numbers(result_text) - source_numbers)
	no_source = [item for item in merged.common if "(" not in item]
	conclusion_sents = sentence_count(merged.conclusion)
	detail_checks = [
		_check("결론 있음", "결론 문장이 있음", f"{len(merged.conclusion.strip())}자", bool(merged.conclusion.strip())),
		_check("결론 길이", "3문장 이내", f"{conclusion_sents}문장", conclusion_sents <= 3),
		_check("공통·차이", "하나 이상", f"공통 {len(merged.common)}개 · 차이 {len(merged.differences)}개", bool(merged.common or merged.differences)),
		_check("공통 의견 출처 표시", "공통 의견마다 '(보고서)' 괄호", "출처 없는 항목: " + (" / ".join(no_source) if no_source else "없음"), not no_source),
		_check("숫자가 요약에 있음", "병합 결과의 숫자가 모두 보고서 요약에 있음",
			f"병합 숫자 {_numbers_text(numbers(result_text))} / 요약에 없는 것 {_numbers_text(invented) if invented else '없음'}", not invented),
	]
	checks = {
		"결론 있음": bool(merged.conclusion.strip()),
		"결론 3문장 이내": sentence_count(merged.conclusion) <= 3,
		"공통·차이 중 하나 이상": bool(merged.common or merged.differences),
		"공통 의견에 출처 표시": all("(" in item for item in merged.common) if merged.common else True,
		"요약에 없는 숫자 없음": not invented,
	}
	return {
		"report_ids": ",".join(map(str, report_ids)), "공통 수": len(merged.common), "차이 수": len(merged.differences),
		**{k: "O" if v else "X" for k, v in checks.items()},
		"요약에 없는 숫자": ", ".join(invented), "시간(초)": round(time.perf_counter() - started, 2),
		"토큰": sum(u.get("total_tokens", 0) for u in usage.values()),
		"비용(원)": round(cost_korean_won(usage, get_usd_krw()), 2) if usage else 0.0,
		"판정(자동)": "통과" if all(checks.values()) else "확인 필요",
		"결론": merged.conclusion, "공통": " / ".join(merged.common), "차이": " / ".join(merged.differences),
		"_checks": detail_checks, "_common": list(merged.common), "_differences": list(merged.differences),
	}


def targets_given(args) -> bool:
	return bool(args.pdf or args.report_id)


def main() -> None:
	parser = argparse.ArgumentParser(description="4.1.4 요약 품질·JSON 형식 점검")
	parser.add_argument("--pdf", type=Path, nargs="*", default=[], help="점검할 보고서 PDF (여러 개 가능)")
	parser.add_argument("--report-id", type=int, nargs="*", default=[], help="DB에 저장된 보고서의 섹션을 쓸 때")
	parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
	parser.add_argument("--sections-only", action="store_true", help="섹션 나누기 결과만 출력 (API 호출 없음)")
	parser.add_argument("--cache-check", type=int, nargs="*", default=[], help="요약 캐시 실제 확인할 report_id (DB 필요)")
	parser.add_argument("--merge", nargs="*", default=[], help="병합 점검할 보고서 묶음. 예) --merge 3,4 3,4,5 (DB 필요)")
	args = parser.parse_args()

	if args.merge:
		rows = []
		for group in args.merge:
			ids = [int(x) for x in group.split(",")]
			row = merge_check(ids)
			rows.append(row)
			print({k: v for k, v in row.items() if k not in ("결론", "공통", "차이") and not k.startswith("_")})
		columns = [c for c in rows[0] if not c.startswith("_")]
		stamp = datetime.now().strftime("%Y%m%d_%H%M")
		write_csv(args.out_dir / f"summary_merge_{stamp}.csv", columns, rows)
		report = write_report(args.out_dir / f"summary_merge_{stamp}_리포트.md", merge_report(rows, f"실행일 {datetime.now():%Y-%m-%d %H:%M}"))
		print(f"저장: {args.out_dir / f'summary_merge_{stamp}.csv'} · 리포트: {report}")
		if not (targets_given(args) or args.cache_check):
			return

	if args.cache_check:
		rows = []
		for report_id in args.cache_check:
			row = cache_check(report_id)
			rows.append(row)
			print(row)
		path = write_report(args.out_dir / f"summary_cache_{datetime.now():%Y%m%d_%H%M}_리포트.md", cache_report(rows))
		print(f"\n리포트 저장: {path}")
		if not targets_given(args):
			return

	targets = [(p.stem, lambda p=p: sections_from_pdf(p)) for p in args.pdf]
	targets += [(f"report_id={i}", lambda i=i: sections_from_db(i)) for i in args.report_id]
	if not targets:
		raise SystemExit("[중단] --pdf 또는 --report-id를 하나 이상 주세요.")

	if args.sections_only:
		for name, load in targets:
			print(f"\n[{name}]")
			for s in load():
				print(f"  {s.section_no}. p.{s.start_page}-{s.end_page} ({len(s.text):,}자) {s.title}")
		return

	from hri.config.settings import load_settings

	counter = CallCounter()
	counter.install()
	section_rows, three_rows = [], []
	for name, load in targets:
		print(f"\n[{name}]")
		rows, three = run_report(name, load(), counter)
		section_rows += rows
		three_rows.append(three)

	stamp = datetime.now().strftime("%Y%m%d_%H%M")
	conditions = (f"실행일 {datetime.now():%Y-%m-%d %H:%M} · 모델={load_settings().llm_model} · temperature=0 · "
		f"대상={', '.join(name for name, _ in targets)}")
	write_csv(args.out_dir / f"summary_sections_{stamp}.csv", SECTION_COLUMNS, section_rows)
	write_csv(args.out_dir / f"summary_three_lines_{stamp}.csv", THREE_COLUMNS, three_rows)
	print("\n" + summarize_results(section_rows, three_rows, conditions))
	report = write_report(args.out_dir / f"summary_{stamp}_리포트.md", build_report(section_rows, three_rows, conditions))
	print(f"저장: {args.out_dir}/summary_*_{stamp}*.csv · 리포트: {report}")


if __name__ == "__main__":
	main()