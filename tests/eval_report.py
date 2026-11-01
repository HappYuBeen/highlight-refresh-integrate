"""평가 스크립트 공통 — 마크다운 리포트 만들기 도우미 (서동안)

모든 평가 스크립트(eval_qa_accuracy·eval_threshold·eval_search_params·eval_summary)가 같은 모양의
'<이름>_<시각>_리포트.md'를 남기도록 표·인용·접기 블록 만드는 함수를 모았다.
리포트 한 문항은 항상 같은 순서로 적는다.

	질문 → 기대 결과 → 실제 출력 → 평가 근거(항목별 기대/실제/판정) → 점수 계산식
"""

from pathlib import Path

PASS, FAIL, PARTIAL, NONE = "✅", "❌", "⚠️", "➖"


def cell(value) -> str:
	"""표 칸에 넣을 수 있게 줄바꿈·'|'를 바꾼다. None은 '-'."""
	if value is None or value == "":
		return "-"
	return str(value).replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def table(headers: list[str], rows: list[list]) -> list[str]:
	out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
	out += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
	return out + [""]


def kv_table(pairs: list[tuple[str, object]]) -> list[str]:
	return table(["항목", "값"], [[k, v] for k, v in pairs])


def quote(text, empty: str = "(빈 출력)") -> list[str]:
	"""여러 줄 텍스트를 인용 블록으로. 답변 원문을 그대로 보여 줄 때 쓴다."""
	text = (text or "").strip()
	if not text:
		return [f"> _{empty}_", ""]
	return [f"> {line}" if line.strip() else ">" for line in text.splitlines()] + [""]


def details(summary: str, body: list[str]) -> list[str]:
	"""접기 블록 (VS Code·GitHub 미리보기에서 펼쳐 봄). 긴 원문·전체 표에 쓴다."""
	return [f"<details><summary>{summary}</summary>", "", *body, "</details>", ""]


def checks_table(checks: list[dict]) -> list[str]:
	"""평가 근거 표: [{'항목', '기대', '실제', '판정'}] → 항목 | 기대 | 실제 | 판정"""
	return table(["평가 항목", "기대", "실제", "판정"],
		[[c["항목"], c["기대"], c["실제"], c["판정"]] for c in checks])


def mark(ok) -> str:
	if ok is None:
		return NONE
	return PASS if ok else FAIL


def score_mark(score) -> str:
	"""1 → ✅, 0.5 → ⚠️, 0 → ❌, None → ➖"""
	if score is None:
		return NONE
	return PASS if score >= 1 else PARTIAL if score > 0 else FAIL


def mean_formula(values: list[float], label: str = "", digits: int = 1, as_percent: bool = True) -> str:
	"""평균 계산식을 글로 쓴다. 예) '(1 + 0.5 + 0) / 3 = 1.5 / 3 = 50.0%'

	항목이 많으면 덧셈 식 대신 합계만 적는다.
	"""
	if not values:
		return f"{label}채점된 값이 없어 계산하지 않음"
	total = sum(values)
	n = len(values)
	result = f"{total / n:.{digits}%}" if as_percent else f"{total / n:,.{digits}f}"
	shown = " + ".join(_num_text(v) for v in values) if n <= 12 else f"합계 {_num_text(total)}"
	return f"{label}({shown}) / {n} = {_num_text(total)} / {n} = **{result}**"


def _num_text(v) -> str:
	if isinstance(v, float) and v.is_integer():
		return str(int(v))
	if isinstance(v, float):
		return f"{v:,.4g}" if abs(v) < 1000 else f"{v:,.2f}"
	return f"{v:,}" if isinstance(v, int) else str(v)


def write_report(path: Path, lines: list[str]) -> Path:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
	return path


def toc(entries: list[tuple[str, str]]) -> list[str]:
	"""목차: [(제목, 앵커용 제목)] — 마크다운 미리보기의 자동 앵커 규칙(소문자·공백→-)을 따른다."""
	return [f"- [{title}](#{_anchor(anchor)})" for title, anchor in entries] + [""]


def _anchor(text: str) -> str:
	import re

	text = text.strip().lower()
	text = re.sub(r"[^\w\s가-힣-]", "", text)
	return re.sub(r"\s", "-", text)
