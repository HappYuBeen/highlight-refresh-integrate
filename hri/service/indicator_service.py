"""지표 비교 · extract_indicators, build_compare_table, plot_indicators (장유빈)"""
import html
import logging
import re
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from hri.dao.indicator_dao import insert_indicators, select_indicators
from hri.domain.schemas import Indicator
from hri.service.report_service import get_report_text, search_reports

from langchain_openai import ChatOpenAI

from hri.llm.llm_client import MODEL

logger = logging.getLogger(__name__)


# =====================================================================
# [장유빈] 지표 추출 · extract_indicators (WBS 3.5.11)
# 보고서 본문(섹션 원문 + 표·차트)에서 경제 지표 수치를 LLM으로 뽑아 indicators 테이블에 저장한다
# =====================================================================

# 지표 추출 답변이 길어질 수 있어 넉넉히 둔다 (gpt-5-mini는 추론 토큰도 max_tokens에 포함)
INDICATOR_MAX_TOKENS = 16000

# 지표 이름 통일표: 대표 이름 → 보고서마다 다르게 쓰는 이름들
# 보고서끼리 지표를 비교하려면 같은 지표가 같은 이름이어야 한다 (이름이 없으면 추가하면 된다)
INDICATOR_ALIASES: dict[str, list[str]] = {
	# 성장·생산·소비·투자
	"경제성장률": ["GDP 성장률", "실질GDP 성장률", "실질 GDP 증가율", "GDP 증가율", "성장률", "국내총생산 성장률", "국내총생산 증가율", "실질성장률"],
	"명목 GDP 성장률": ["명목GDP 증가율", "명목 GDP 증가율", "명목성장률"],
	"민간소비 증가율": ["민간소비", "민간소비 성장률", "민간소비지출 증가율", "민간 최종소비지출 증가율"],
	"설비투자 증가율": ["설비투자", "설비투자 성장률"],
	"건설투자 증가율": ["건설투자", "건설투자 성장률"],
	"광공업생산 증가율": ["광공업생산", "광공업 생산"],
	"소매판매 증가율": ["소매판매", "소매판매액 증가율"],
	# 물가
	"소비자물가 상승률": ["소비자물가", "소비자물가상승률", "CPI", "CPI 상승률", "물가 상승률", "물가상승률", "소비자물가지수 상승률", "소비자물가 증가율", "소비자물가지수 증가율", "인플레이션율"],
	"근원물가 상승률": ["근원 소비자물가 상승률", "근원CPI", "근원 CPI 상승률", "근원물가", "근원인플레이션"],
	"생산자물가 상승률": ["PPI", "PPI 상승률", "생산자물가", "생산자물가 증가율"],
	# 고용
	"실업률": ["전체 실업률"],
	"고용률": ["전체 고용률"],
	"취업자 수 증감": ["취업자 증가", "취업자 증감", "취업자수 증가", "취업자 수 증가", "취업자 증가폭"],
	# 금리·환율·시장
	"기준금리": ["정책금리", "한국은행 기준금리", "한은 기준금리", "BOK 기준금리"],
	"국고채 3년 금리": ["국고채(3년) 금리", "국고채 3년물 금리", "국고채 3년물", "국고채 금리(3년)", "국고채3년"],
	"원/달러 환율": ["원달러 환율", "원-달러 환율", "원·달러 환율", "원/달러", "달러 환율", "USD/KRW", "달러당 원화 환율"],
	"코스피": ["KOSPI", "코스피 지수", "KOSPI 지수"],
	"두바이유 가격": ["두바이유", "두바이유가", "두바이유 가격(배럴당)"],
	"WTI 유가": ["WTI", "WTI 가격", "WTI유가"],
	# 대외
	"경상수지": ["경상수지 흑자", "경상수지 규모", "경상수지 흑자 규모"],
	"무역수지": ["무역수지 흑자", "무역수지 규모"],
	"수출 증가율": ["수출증가율", "수출 증가", "수출 증감률", "총수출 증가율", "수출액 증가율"],
	"수입 증가율": ["수입증가율", "수입 증가", "수입 증감률", "총수입 증가율", "수입액 증가율"],
	"수출액": ["총수출", "총수출액", "수출 규모"],
	"수입액": ["총수입", "총수입액", "수입 규모"],
	# 재정·가계
	"국가채무 비율": ["국가채무비율", "GDP 대비 국가채무", "GDP 대비 국가채무 비율", "국가채무/GDP"],
	"관리재정수지 비율": ["GDP 대비 관리재정수지", "GDP 대비 관리재정수지 비율"],
	"가계부채": ["가계부채 규모", "가계부채 잔액"],
	"소비자심리지수": ["CCSI", "소비자심리", "소비자심리지수(CCSI)"],
}

INDICATOR_SYSTEM_PROMPT = (
	"당신은 경제 분석 보고서에서 경제 지표 수치를 뽑는 도우미입니다.\n"
	"사용자가 주는 [보고서 본문]에서 지표 수치를 모두 뽑아 목록으로 돌려줍니다.\n"
	"규칙:\n"
	"1. 본문에 실제로 적힌 수치만 뽑습니다. 추측·계산·외부 지식으로 만든 값은 넣지 않습니다.\n"
	"2. name은 보고서에 쓰인 이름 그대로 적습니다.\n"
	"3. value에는 숫자만 적습니다. 단위·기호는 빼고 음수 부호는 남깁니다.\n"
	"4. unit에는 값 옆에 붙어 있던 단위를 적습니다 (예: %, %p, 원, 억 원, 조 원).\n"
	"5. period는 연 YYYY, 분기 YYYYQn, 월 YYYYMM, 일 YYYYMMDD 중 하나로 적습니다. "
	"기준 시점을 알 수 없는 수치는 뽑지 않습니다.\n"
	"6. 실적치와 전망치는 기준 시점으로 구분해 모두 뽑습니다.\n"
	"7. source는 수치를 찾은 위치입니다. 일반 본문은 text, '[표 ...]' 블록은 table, '[차트 ...]' 블록은 chart입니다.\n"
	"8. 제목 줄에 '(추정)'이 붙은 차트 블록에서 읽은 수치는 needs_review를 true로, 그 밖의 수치는 false로 합니다.\n"
	"9. 같은 지표·같은 시점·같은 값은 한 번만 뽑습니다.\n"
	"10. page는 수치가 적힌 쪽 번호입니다. '[표 p.12 ...]'·'[차트 p.12 ...]' 블록의 수치는 12, "
	"일반 본문의 수치는 그 본문이 속한 '## 제목 (p.3-5)' 줄의 시작 쪽 3입니다. 알 수 없으면 null입니다.\n"
	"11. 뽑을 수치가 없으면 빈 목록을 돌려줍니다."
)

# 허용하는 기준 시점 형식: 연 YYYY · 분기 YYYYQn · 월 YYYYMM · 일 YYYYMMDD
_PERIOD_PATTERN = re.compile(
	r"^(\d{4}"
	r"|\d{4}Q[1-4]"
	r"|\d{4}(0[1-9]|1[0-2])"
	r"|\d{4}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01]))$"
)


class IndicatorList(BaseModel):
	"""LLM이 한 번에 돌려주는 지표 목록. with_structured_output의 출력 형식으로 쓴다."""

	indicators: list[Indicator] = Field(description="본문에서 뽑은 지표 목록. 뽑을 수치가 없으면 빈 목록")


def _name_key(name: str) -> str:
	"""이름 비교용 열쇠: 공백을 모두 없애고 소문자로 바꾼다 ('소비자물가 상승률' == '소비자물가상승률')."""
	return re.sub(r"\s+", "", name).lower()


# 열쇠 → 대표 이름. 대표 이름 자신도 넣어 두어 공백만 다른 경우도 하나로 모은다
_ALIAS_LOOKUP: dict[str, str] = {
	_name_key(alias): canonical
	for canonical, aliases in INDICATOR_ALIASES.items()
	for alias in [canonical, *aliases]
}


def normalize_indicator_name(name: str) -> str:
	"""지표 이름을 INDICATOR_ALIASES의 대표 이름으로 통일한다. 표에 없는 이름은 앞뒤 공백만 지워 그대로 돌려준다."""
	stripped = name.strip()
	return _ALIAS_LOOKUP.get(_name_key(stripped), stripped)


def _clean_indicators(indicators: list[Indicator], report_id: int) -> list[Indicator]:
	"""LLM이 뽑은 지표를 저장하기 좋게 다듬는다.

	- report_id를 붙인다 (LLM은 채우지 않는다)
	- 이름은 대표 이름으로 통일하고, 기준 시점은 대문자로 맞춘다 (2026q2 → 2026Q2)
	- 기준 시점 형식이 틀렸거나 이름이 빈 지표는 버린다 (ECOS 비교 기간을 만들 수 없다)
	- 이름·시점·값·단위가 같은 지표는 먼저 나온 것 하나만 남긴다
	- 쪽 번호가 1보다 작으면 None으로 바꾼다 (잘못 읽은 쪽을 비교표에 보이지 않게 한다)
	"""
	cleaned: list[Indicator] = []
	seen: set[tuple[str, str, float, str]] = set()
	for item in indicators:
		name = normalize_indicator_name(item.name)
		period = item.period.strip().upper()
		unit = item.unit.strip()
		if not name or not _PERIOD_PATTERN.match(period):
			continue
		key = (name, period, item.value, unit)
		if key in seen:
			continue
		seen.add(key)
		page = item.page if item.page is not None and item.page >= 1 else None
		cleaned.append(item.model_copy(update={"report_id": report_id, "name": name, "period": period, "unit": unit, "page": page}))
	return cleaned


def _request_indicators(text: str) -> IndicatorList | None:
	"""보고서 본문을 LLM에 보내 지표 목록을 받는다. 결과를 읽지 못하면 None.

	llm_client는 import할 때 .env를 읽는 모듈에 있어서, 이 함수 안에서 불러온다
	(테스트가 .env 없이도 extract_indicators의 나머지 로직을 확인할 수 있게 하려는 것).
	지표 추출은 본문의 수치를 옮겨 적는 일이라 추론 강도를 낮춘다.
	(gpt-5-mini는 추론에 max_tokens를 다 써 버리면 답이 비어 LengthFinishReasonError가 난다)
	"""

	model = ChatOpenAI(
		model=MODEL,
		max_tokens=INDICATOR_MAX_TOKENS,
		reasoning_effort="low",
	).with_structured_output(IndicatorList)
	messages = [
		{"role": "system", "content": INDICATOR_SYSTEM_PROMPT},
		{"role": "user", "content": f"[보고서 본문]\n{text}"},
	]
	return model.invoke(messages)


def extract_indicators(report_id: int) -> list[Indicator]:
	"""보고서에서 경제 지표 수치를 뽑아 돌려주고 indicators 테이블에 저장한다.

	이미 저장된 지표가 있으면 LLM을 부르지 않고 그대로 돌려준다 (같은 보고서를 다시 뽑지 않는다).
	없으면 get_report_text()의 본문을 LLM에 보내 Indicator 목록을 받고,
	_clean_indicators()로 이름·시점을 다듬은 뒤 insert_indicators()로 저장한다.
	본문이 비었거나 LLM 결과를 읽지 못하면 저장하지 않고 빈 리스트를 돌려준다.
	비교표·차트(build_compare_table, plot_indicators)가 이 결과를 사용한다.
	"""
	saved = select_indicators(report_id)
	if saved:
		return saved

	text = get_report_text(report_id)
	if not text.strip():
		return []

	result = _request_indicators(text)
	if result is None:
		return []

	indicators = _clean_indicators(result.indicators, report_id)
	insert_indicators(report_id, indicators)
	return indicators


# =====================================================================
# [장유빈] 지표 비교 · build_compare_table, plot_indicators (WBS 3.5.12)
# 선택한 보고서들의 지표를 같은 이름·시점·단위끼리 모아 표(HTML)와 막대 차트(PNG)로 보여 준다
# =====================================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # hri/service/indicator_service.py → 프로젝트 맨 위 폴더
FONT_DIR = PROJECT_ROOT / "assets" / "fonts"  # 한글 폰트 파일(.ttf·.otf)을 넣어 두는 곳 (서버에는 한글 폰트가 없다)
PLOT_DIR = PROJECT_ROOT / "data" / "plots"  # 차트 PNG 저장 폴더 (.gitignore로 커밋 제외)
# assets/fonts에 파일이 없을 때 찾아볼 컴퓨터에 설치된 한글 폰트 이름
SYSTEM_FONT_NAMES = ["NanumGothic", "Malgun Gothic", "AppleGothic", "Noto Sans CJK KR", "Noto Sans KR"]

EMPTY_CELL = "-"  # 그 보고서에 없는 지표 칸
REVIEW_MARK = "*"  # 차트에서 읽어 확인이 필요한 값(needs_review) 표시
REVIEW_NOTE = f"{REVIEW_MARK} 차트에서 읽은 추정값이라 확인이 필요합니다."
DIFF_HEADER = "차이(최대−최소)"  # 보고서 2개 이상을 비교할 때 표 맨 끝에 붙이는 열 이름
ECOS_NOTE = "ECOS 값은 한국은행 경제 통계 시스템의 데이터입니다. 보고서 수치와 기준(계절조정·전기비 등)이 다를 수 있습니다."
MAX_ECOS_ROWS = 6  # ECOS 값은 최근 몇 줄까지 보여 줄지
MAX_UNIT_GROUPS = 3  # 차트 하나에 그리는 단위 묶음 수 (단위가 같은 지표끼리 한 칸)
MAX_BARS_PER_GROUP = 8  # 한 칸에 그리는 지표·시점 수 (너무 많으면 글자가 겹친다)
MAX_BAR_WIDTH = 0.25  # 막대 하나의 최대 두께 (가로축 한 자리 = 1). 보고서가 적어도 막대 사이에 여백이 남는다
MIN_BAR_SLOTS = 4  # 지표·시점이 적어도 가로축을 이만큼은 잡아 막대가 너무 넓어지지 않게 한다 (남는 자리는 빈칸)
LEGEND_TITLE_LENGTH = 20  # 범례에 쓰는 보고서 제목의 최대 글자 수
TITLE_LENGTH = 15  # 비교 표 머리글의 보고서 이름 칸 너비(글자 수). 넘으면 앞 TITLE_KEEP글자 + '...'
TITLE_KEEP = 12
# 비교 표 열 너비 계산 (단위 em). 한글·한자는 1글자 1em, 그 밖의 글자는 0.6em으로 어림한다
WIDE_CHAR_EM = 1.0
NARROW_CHAR_EM = 0.6
CELL_PAD_EM = 1.6  # 칸 좌우 여백
NAME_SPARE_EM = 1.0  # 지표 열은 가장 긴 지표 이름보다 이만큼 여유를 둔다
NAME_MAX_RATIO = 0.2  # 지표 열은 표 전체 너비의 20%를 넘지 않는다

# (지표 이름, 시점, 단위) → {report_id: Indicator}
_Rows = dict[tuple[str, str, str], dict[int, Indicator]]


def _format_value(value: float) -> str:
	"""값을 읽기 좋게 바꾼다: 1390.0 → '1,390', 2.50 → '2.5', -0.3 → '-0.3'."""
	text = f"{value:,.2f}".rstrip("0").rstrip(".")
	return "0" if text in ("-0", "") else text


def _fit_title(title: str) -> str:
	"""비교 표 머리글용 이름: TITLE_LENGTH글자를 넘으면 앞 TITLE_KEEP글자 + '...'로 줄인다."""
	return title if len(title) <= TITLE_LENGTH else title[:TITLE_KEEP] + "..."


def _report_titles(report_ids: list[int], fit: bool = False) -> dict[int, str]:
	"""report_id → 표 머리글·차트 범례에 쓸 보고서 이름.

	보고서 선택 창에 보이는 이름과 같게 보고서 제목을 쓴다. fit이면 _fit_title()로 길이를 맞춘다.
	(줄인 뒤) 이름이 같은 보고서가 있으면 '이름 (#번호)'로 구분한다.
	"""
	found = {r.report_id: r for r in search_reports("")}
	titles: dict[int, str] = {}
	for rid in report_ids:
		report = found.get(rid)
		title = (report.title if report else "") or f"보고서 #{rid}"
		titles[rid] = _fit_title(title) if fit else title
	counts: dict[str, int] = {}
	for title in titles.values():
		counts[title] = counts.get(title, 0) + 1
	return {rid: f"{t} (#{rid})" if counts[t] > 1 else t for rid, t in titles.items()}


def _collect_rows(report_ids: list[int]) -> _Rows:
	"""보고서마다 extract_indicators()로 지표를 가져와 (이름, 시점, 단위)별로 모은다.

	한 보고서에 같은 (이름, 시점, 단위)가 여러 개면 먼저 나온 값 하나만 쓴다 (pivot_table의 aggfunc='first'와 같다).
	"""
	rows: _Rows = {}
	for report_id in report_ids:
		for item in extract_indicators(report_id):
			rows.setdefault((item.name, item.period, item.unit), {}).setdefault(report_id, item)
	return rows


def _sorted_keys(rows: _Rows) -> list[tuple[str, str, str]]:
	"""공통 지표가 위로 오게 정렬한다.

	1) 같은 (이름, 시점, 단위)가 나온 보고서가 많은 행, 2) 시점·단위가 달라도 같은 이름이 나온 보고서가 많은 지표,
	3) 이름·시점·단위 순. 그래서 보고서마다 시점만 다른 지표도 혼자만 나온 지표보다 위에 모여 보인다.
	"""
	name_reports: dict[str, set[int]] = {}
	for (name, _, _), by_report in rows.items():
		name_reports.setdefault(name, set()).update(by_report)
	return sorted(rows, key=lambda key: (-len(rows[key]), -len(name_reports[key[0]]), key))


def _escape_cell(text: str) -> str:
	"""Markdown 표 칸에 넣을 글자에서 표를 깨는 '|'와 줄바꿈을 바꾼다."""
	return text.replace("|", "\\|").replace("\n", " ")


def _rows_to_markdown(header: list[str], body: list[list[str]]) -> str:
	"""머리글과 행 목록을 Markdown 표 문자열로 만든다. (table_to_markdown(엄정현)이 들어오면 그것으로 바꾼다)"""
	lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
	lines.extend("| " + " | ".join(row) + " |" for row in body)
	return "\n".join(lines)


def _text_em(text: str) -> float:
	"""글자가 차지할 너비를 em으로 어림한다 (한글·한자 1em, 나머지 0.6em)."""
	return sum(WIDE_CHAR_EM if _is_wide(ch) else NARROW_CHAR_EM for ch in text)


def _is_wide(ch: str) -> bool:
	"""한글 자모·한중일 문자·한글 음절·전각 글자면 True."""
	code = ord(ch)
	return 0x1100 <= code <= 0x11FF or 0x2E80 <= code <= 0xD7AF or 0xF900 <= code <= 0xFAFF or 0xFF00 <= code <= 0xFFEF


def _column_widths(header: list[str], body: list[list[str]], report_count: int) -> list[float]:
	"""비교 표 열 너비(em)를 정한다.

	- 보고서 열(4번째부터 report_count개)은 이름 TITLE_LENGTH글자에 맞춘 같은 너비
	- 지표 열은 가장 긴 지표 이름 + 여유, 다만 표 전체의 NAME_MAX_RATIO(20%)를 넘지 않는다
	- 나머지(시점·단위·차이) 열은 머리글·칸 중 가장 긴 글자에 맞춘다
	"""
	columns = list(zip(header, *body))
	widths = [max(_text_em(text) for text in column) + CELL_PAD_EM for column in columns]
	for i in range(3, 3 + report_count):
		widths[i] = TITLE_LENGTH * WIDE_CHAR_EM + CELL_PAD_EM
	name_width = max(_text_em(name) for name in columns[0][1:]) + CELL_PAD_EM + NAME_SPARE_EM
	# 지표 열 = 표 전체 × 비율  →  지표 열 = 나머지 열 합 × 비율 / (1 − 비율)
	name_max = sum(widths[1:]) * NAME_MAX_RATIO / (1 - NAME_MAX_RATIO)
	widths[0] = min(name_width, name_max)
	return widths


def _rows_to_html(header: list[str], body: list[list[str]], widths: list[float], titles: list[str | None]) -> str:
	"""머리글·행을 열 너비가 고정된 HTML 표로 만든다. titles는 머리글에 마우스를 올리면 보일 전체 이름(없으면 None)."""
	cols = "".join(f'<col style="width:{w:.1f}em">' for w in widths)
	head = "".join(
		f'<th title="{html.escape(full)}">{html.escape(text)}</th>' if full else f"<th>{html.escape(text)}</th>"
		for text, full in zip(header, titles)
	)
	lines = [
		f'<table class="compare-grid" style="width:{sum(widths):.1f}em;table-layout:fixed">',
		f"<colgroup>{cols}</colgroup>",
		f"<thead><tr>{head}</tr></thead>",
		"<tbody>",
		*("<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>" for row in body),
		"</tbody>",
		"</table>",
	]
	return "\n".join(lines)


def _format_cell(item: Indicator) -> str:
	"""표 칸에 넣을 글자: 값 + 확인 필요 '*' + 출처 쪽. 예: '2.4', '1,390*', '2.4 (p.12)'."""
	text = _format_value(item.value) + (REVIEW_MARK if item.needs_review else "")
	return f"{text} (p.{item.page})" if item.page else text


def _diff_cell(items: list[Indicator], unit: str) -> str:
	"""보고서들의 값 차이(최대값 − 최소값)를 칸 글자로 만든다. 값이 2개 미만이면 '-'.

	단위가 %이면 차이는 %p로 적고, 그 밖의 단위는 그대로 붙인다. 차이를 만든 값 중 확인이 필요한 값이 있으면 '*'를 붙인다.
	"""
	if len(items) < 2:
		return EMPTY_CELL
	values = [item.value for item in items]
	suffix = "%p" if unit == "%" else unit
	mark = REVIEW_MARK if any(item.needs_review for item in items) else ""
	return f"{_format_value(max(values) - min(values))}{suffix}{mark}"


def build_compare_table(report_ids: list[int]) -> str:
	"""선택한 보고서들의 지표를 한 표(HTML, Markdown 칸에 그대로 넣어 보여 준다)로 비교한다.

	행은 (지표 이름, 시점, 단위), 열은 보고서 제목이다. 그 보고서에 없는 칸은 '-'로 채우고,
	차트에서 읽어 확인이 필요한 값(needs_review)에는 '*'를 붙인 뒤 표 아래에 설명을 적는다.
	열 머리글은 보고서 선택 창에 보이는 보고서 제목을 15글자에 맞춘 것이다 (넘으면 앞 12글자 + '...', 전체 이름은 마우스를 올리면 보임).
	출처 쪽을 아는 값은 '2.4 (p.12)'처럼 쪽을 함께 적고, 보고서가 2개 이상이면 맨 끝에 값 차이(최대−최소) 열을 붙인다.
	공통 지표가 위로 온다 (_sorted_keys). 열 너비는 _column_widths()로 정한다. 지표가 하나도 없으면 빈 문자열을 돌려준다.
	"""
	report_ids = list(dict.fromkeys(report_ids))  # 중복 번호 제거 (순서 유지)
	rows = _collect_rows(report_ids)
	if not rows:
		return ""

	titles = _report_titles(report_ids, fit=True)
	full_titles = _report_titles(report_ids)
	header = ["지표", "시점", "단위", *(titles[rid] for rid in report_ids)]
	header_titles: list[str | None] = [None, None, None, *(full_titles[rid] for rid in report_ids)]
	with_diff = len(report_ids) >= 2  # 보고서가 하나뿐이면 비교할 대상이 없어 차이 열을 두지 않는다
	if with_diff:
		header.append(DIFF_HEADER)
		header_titles.append(None)
	body = []
	has_review = False
	for key in _sorted_keys(rows):
		cells = []
		for report_id in report_ids:
			item = rows[key].get(report_id)
			if item is None:
				cells.append(EMPTY_CELL)
				continue
			has_review = has_review or item.needs_review
			cells.append(_format_cell(item))
		if with_diff:
			cells.append(_diff_cell(list(rows[key].values()), key[2]))
		body.append([key[0], key[1], key[2] or EMPTY_CELL, *cells])

	table = _rows_to_html(header, body, _column_widths(header, body, len(report_ids)), header_titles)
	return f"{table}\n\n{REVIEW_NOTE}" if has_review else table

def build_ecos_reference(report_ids: list[int]) -> str:
	"""비교한 보고서의 지표 중 ECOS로 확인할 지표 1개를 골라, 보고서 값과 ECOS 값을 한 표(Markdown)로 만든다.

	find_usable_data()가 고른 지표(query.indicator_name)와 이름이 같은 보고서 수치를 모아 ECOS 값 옆에 보여 준다.
	ECOS로 확인할 통계가 없거나 값을 못 가져오면 빈 문자열을 돌려준다 (비교표는 그대로 보여 주면 된다).
	ECOS 호출 실패(ExternalApiError)는 그대로 올라가므로 부르는 쪽에서 잡는다.
	"""
	from hri.api.ecos_client import fetch_indicators
	from hri.api.ecos_finder import find_usable_data

	report_ids = list(dict.fromkeys(report_ids))
	by_report = {rid: extract_indicators(rid) for rid in report_ids}
	all_items = [item for items in by_report.values() for item in items]
	if not all_items:
		return ""

	query = find_usable_data(all_items)
	if query is None:
		return ""
	ecos_values = fetch_indicators(query, query.indicator_name)
	if not ecos_values:
		return ""

	name = query.indicator_name.strip()
	titles = _report_titles(report_ids)
	body = []
	for rid in report_ids:
		for item in by_report[rid]:
			if item.name.strip() == name:
				body.append([_escape_cell(titles[rid]), item.period, _format_cell(item), _escape_cell(item.unit) or EMPTY_CELL])
	for item in ecos_values[-MAX_ECOS_ROWS:]:
		body.append([f"ECOS {query.stat_code}", item.period, _format_value(item.value), _escape_cell(item.unit) or EMPTY_CELL])

	table = _rows_to_markdown(["구분", "시점", "값", "단위"], body)
	reason = f"비교한 지표: {name}" + (f" ({query.reason})" if query.reason else "")
	return f"{reason}\n\n{table}\n\n{ECOS_NOTE}"


def _use_korean_font() -> str | None:
	"""차트에 쓸 한글 폰트 이름을 정해 돌려준다. 없으면 None.

	assets/fonts의 .ttf·.otf 파일을 먼저 등록해 쓰고, 파일이 없으면 컴퓨터에 설치된 한글 폰트를 찾는다.
	"""
	from matplotlib import font_manager

	for path in sorted([*FONT_DIR.glob("*.ttf"), *FONT_DIR.glob("*.otf")]):
		try:
			font_manager.fontManager.addfont(str(path))
			return font_manager.FontProperties(fname=str(path)).get_name()
		except (OSError, RuntimeError):
			logger.warning("폰트 파일을 읽지 못했습니다: %s", path)
	installed = {f.name for f in font_manager.fontManager.ttflist}
	for name in SYSTEM_FONT_NAMES:
		if name in installed:
			return name
	logger.warning("한글 폰트를 찾지 못했습니다. assets/fonts에 폰트 파일(예: NanumGothic.ttf)을 넣어 주세요.")
	return None


def _shorten(text: str, length: int = LEGEND_TITLE_LENGTH) -> str:
	"""긴 제목은 length글자에서 자르고 '…'를 붙인다."""
	return text if len(text) <= length else text[: length - 1] + "…"


def plot_indicators(report_ids: list[int]) -> str | None:
	"""선택한 보고서들의 지표를 막대 차트(PNG)로 그리고 파일 경로를 돌려준다.

	단위가 같은 지표끼리만 한 칸에 그린다 (%와 원을 한 축에 섞지 않는다). 칸마다 가로축은 '지표·시점',
	막대 색은 보고서다. 2개 이상의 보고서에 함께 나온 지표만 그리고, 차트에서 읽은 추정값(needs_review)은
	빗금 막대에 '*'를 붙인다. 모든 칸은 가로축 자리 수(최소 MIN_BAR_SLOTS)가 같아 막대 두께가 같고,
	지표가 적은 칸은 오른쪽이 빈칸으로 남는다. 파일 이름에 uuid를 붙여 동시에 써도 겹치지 않는다.
	그릴 지표가 없으면(공통 지표 없음) None을 돌려준다.
	"""
	import matplotlib

	matplotlib.use("Agg")  # 화면 없는 서버에서도 그릴 수 있게 한다
	from matplotlib.figure import Figure

	report_ids = list(dict.fromkeys(report_ids))
	rows = _collect_rows(report_ids)
	shared = [key for key in _sorted_keys(rows) if len(rows[key]) >= 2]
	if not shared:
		return None

	# 단위별로 묶고, 지표가 많은 단위부터 MAX_UNIT_GROUPS개만 그린다
	by_unit: dict[str, list[tuple[str, str, str]]] = {}
	for key in shared:
		by_unit.setdefault(key[2], []).append(key)
	units = sorted(by_unit, key=lambda unit: -len(by_unit[unit]))[:MAX_UNIT_GROUPS]

	titles = _report_titles(report_ids)
	font = _use_korean_font()
	rc = {"axes.unicode_minus": False}
	if font:
		rc["font.family"] = font

	with matplotlib.rc_context(rc):
		# 모든 칸의 가로축 자리 수를 같게 해 막대 두께를 맞춘다. 지표가 적은 칸은 오른쪽이 빈칸으로 남는다
		slots = max(MIN_BAR_SLOTS, max(min(len(by_unit[u]), MAX_BARS_PER_GROUP) for u in units))
		fig = Figure(figsize=(min(14, max(7, 1.4 * slots + 3)), 3.8 * len(units)))
		axes = fig.subplots(len(units), 1, squeeze=False)[:, 0]
		colors = [f"C{i}" for i in range(len(report_ids))]
		has_review = False
		width = min(0.8 / len(report_ids), MAX_BAR_WIDTH)

		for ax, unit in zip(axes, units):
			keys = by_unit[unit][:MAX_BARS_PER_GROUP]
			labels = [f"{name}\n{period}" for name, period, _ in keys]
			for i, report_id in enumerate(report_ids):
				xs, heights, reviews = [], [], []
				for j, key in enumerate(keys):
					item = rows[key].get(report_id)
					if item is not None:
						xs.append(j + (i - (len(report_ids) - 1) / 2) * width)
						heights.append(item.value)
						reviews.append(item.needs_review)
				bars = ax.bar(xs, heights, width, color=colors[i], label=_shorten(titles[report_id]))
				for bar, review in zip(bars, reviews):
					if review:
						has_review = True
						bar.set_hatch("//")
						bar.set_alpha(0.6)
				ax.bar_label(bars, labels=[_format_value(h) + (REVIEW_MARK if r else "") for h, r in zip(heights, reviews)], fontsize=8, padding=2)
			ax.set_xticks(range(len(keys)), labels, fontsize=9)
			ax.set_xlim(-0.5, slots - 0.5)
			ax.set_ylabel(unit or "단위 없음")
			ax.axhline(0, color="gray", linewidth=0.6)
			ax.margins(y=0.15)

		axes[0].legend(fontsize=8, loc="best")
		fig.suptitle("보고서별 지표 비교", fontsize=13)
		if has_review:
			fig.text(0.99, 0.005, REVIEW_NOTE, ha="right", va="bottom", fontsize=8, color="gray")
		fig.tight_layout(rect=(0, 0.02, 1, 0.97))

		PLOT_DIR.mkdir(parents=True, exist_ok=True)
		path = PLOT_DIR / f"compare_{uuid.uuid4().hex}.png"
		fig.savefig(path, dpi=150)
	return str(path)