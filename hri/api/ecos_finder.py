"""ECOS 활용 데이터 판단 · find_usable_data (류현수)

보고서에서 뽑은 지표(Indicator) 목록을 받아, 한국은행 ECOS 통계 가운데 보고서 수치를 확인하는 데
가장 도움이 되는 통계 1개를 골라 조회 조건(EcosQuery)으로 돌려준다.
실제 값 조회는 ecos_client.fetch_indicators(query, query.indicator_name)로 따로 한다.

흐름
	1) 통계표 목록 불러오기: StatisticTableList 전체를 data/ecos/에 캐시해 두고 쓴다 (TABLE_CACHE_DAYS일마다 새로)
	2) 검색어 만들기: 지표 이름마다 LLM이 ECOS 통계표 이름에 쓰일 만한 검색어를 만든다 (한 번에 모아서 1회 호출)
	   예: 경제성장률 → 국내총생산, GDP, 경제성장률
	3) 후보 모으기: 지표(이름·단위·주기가 같은 것끼리 한 묶음)마다 주기가 맞고 검색어가 많이 맞는 통계표
	   MAX_TABLES개, 그 표에서 지표 시점이 수록 기간 안에 드는 항목 MAX_ITEMS_PER_TABLE개
	4) 판별: 모든 지표와 후보를 LLM에 한 번에 보여 주고, 가장 연관된 '지표 1개 + 통계표·항목 1개'를 고르게 한다 (_EcosPick)
	5) 검사: 고른 지표 번호·코드가 후보에 실제로 있는지 코드로 확인한 뒤 EcosQuery로 만든다
	   기간은 고른 지표의 시점 중 수록 기간 안에 드는 가장 최근 시점

사용 예
	indicators = extract_indicators(report_id)
	query = find_usable_data(indicators)            # 쓸 만한 통계가 없으면 None
	if query:
		ecos_values = fetch_indicators(query, query.indicator_name)
"""

import json
import logging
import re
import time
from dataclasses import asdict
from pathlib import Path

from ecos import EcosClient
from ecos.exceptions import EcosResponseError
from ecos.models import StatisticItem, StatisticTable
from pydantic import BaseModel, Field

from hri.config.settings import load_settings
from hri.domain.schemas.external import EcosQuery, Indicator
from hri.exception.errors import ExternalApiError

__all__ = ["find_usable_data", "load_table_list", "load_item_list"]

logger = logging.getLogger(__name__)

# 캐시 폴더: 프로젝트 루트의 data/ecos/ (.gitignore에 들어 있음)
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "ecos"
# 통계표·항목 목록은 자주 바뀌지 않아서 이 기간 동안은 캐시를 쓴다
TABLE_CACHE_DAYS = 30
# 한 번에 받는 목록 줄 수 (ECOS는 요청마다 시작·끝 번호를 받는다)
PAGE_SIZE = 1000
# 지표 묶음 1개당 LLM에 보여 줄 후보 통계표 수, 통계표 1개당 후보 항목 수
# (모든 지표의 후보를 한 번에 보여 주므로 너무 크면 프롬프트가 길어진다)
MAX_TABLES = 3
MAX_ITEMS_PER_TABLE = 20
# 판별 답변 길이 (gpt-5-mini는 추론 토큰도 max_tokens에 포함)
FINDER_MAX_TOKENS = 3000

# Indicator.period 형식 → ECOS 주기
_PERIOD_CYCLES = [
	(re.compile(r"^\d{4}$"), "A"),
	(re.compile(r"^\d{4}Q[1-4]$"), "Q"),
	(re.compile(r"^\d{6}$"), "M"),
	(re.compile(r"^\d{8}$"), "D"),
]

KEYWORD_SYSTEM_PROMPT = (
	"당신은 한국은행 경제통계시스템(ECOS)의 통계표를 찾는 도우미입니다.\n"
	"보고서에 나온 경제 지표 이름마다, ECOS 통계표 이름에 들어 있을 만한 검색어를 2~5개 만듭니다.\n"
	"규칙:\n"
	"1. 통계표 이름에 쓰이는 공식 명칭을 우선합니다 (예: 경제성장률 → 국내총생산, GDP, 경제성장률).\n"
	"2. 검색어는 짧은 명사로 씁니다. '증가율', '전년동월비' 같은 계산 방식만으로 된 검색어는 만들지 않습니다.\n"
	"3. 받은 지표 이름은 name에 글자 그대로 옮깁니다."
)

CHOICE_SYSTEM_PROMPT = (
	"당신은 보고서의 경제 지표 수치를 한국은행 ECOS 통계와 비교해 확인하려는 분석 도우미입니다.\n"
	"[지표] 목록과 지표별 [후보]를 보고, 보고서 수치를 확인하는 데 가장 도움이 되는 '지표 1개 + 통계표·항목 1개'를 고릅니다.\n"
	"규칙:\n"
	"1. indicator_no에는 [지표 n]의 번호 n을, 코드는 그 지표의 [후보]에 적힌 통계표 코드·항목코드를 글자 그대로 씁니다.\n"
	"2. 뜻과 단위가 정확히 맞는 짝을 우선합니다. 지표가 증감률(%)이면 증감률 항목을, 금액·지수면 같은 종류의 항목을 고릅니다.\n"
	"3. 실질/명목, 원계열/계절조정, 전기비/전년동기비가 지표와 맞는 항목을 고릅니다. 알 수 없으면 가장 일반적인 항목을 고릅니다.\n"
	"4. 여러 짝이 맞으면 보고서의 핵심 거시 지표(성장률·물가·금리·환율 등)이면서 항목 이름이 지표와 가장 직접 같은 것을 고릅니다.\n"
	"5. 두 번째 단계 항목(Group2)은 첫 번째 항목만으로 값이 하나로 정해지지 않을 때만 item_code2에 씁니다.\n"
	"6. 비교할 만한 짝이 하나도 없으면 usable을 false로 하고 번호·코드는 비워 둡니다.\n"
	"7. reason에는 고른 이유나 없는 이유를 한 문장으로 씁니다."
)


class _EcosPick(BaseModel):
	"""LLM이 지표 목록과 지표별 ECOS 후보 가운데 가장 연관된 지표 1개 + 통계표·항목 1개를 고른 결과.

	with_structured_output의 출력 형식으로만 쓰고, 검사한 뒤 EcosQuery로 바꿔 돌려준다 (밖으로 내보내지 않는다).
	"""

	usable: bool = Field(description="보고서 지표와 같은 뜻·단위로 비교할 수 있는 짝이 있으면 true, 없으면 false")
	indicator_no: int | None = Field(default=None, description="고른 지표의 번호 ([지표 n]의 n). usable=false면 null")
	stat_code: str | None = Field(default=None, description="고른 통계표 코드를 후보 목록에서 그대로 (예: 200Y102). usable=false면 null")
	item_code: str | None = Field(default=None, description="고른 첫 번째 항목코드를 후보 목록에서 그대로 (예: 10111). usable=false면 null")
	item_code2: str | None = Field(default=None, description="두 번째 단계 항목코드가 필요할 때만 (예: 실업률 계절조정 I28B). 필요 없으면 null")
	reason: str = Field(description="이 짝을 고른 이유, 또는 쓸 만한 짝이 없는 이유 한 문장")


class _KeywordSet(BaseModel):
	"""지표 이름 1개의 ECOS 검색어."""

	name: str = Field(description="받은 지표 이름 그대로")
	keywords: list[str] = Field(description="ECOS 통계표 이름에 있을 만한 검색어 2~5개")


class _KeywordList(BaseModel):
	"""LLM이 한 번에 돌려주는 지표별 검색어 목록. with_structured_output의 출력 형식으로 쓴다."""

	items: list[_KeywordSet] = Field(description="지표 이름마다 하나씩")


# ---------------------------------------------------------------------
# 1) · 4) 목록 불러오기 (data/ecos/ 캐시)
# ---------------------------------------------------------------------
def _client() -> EcosClient:
	return EcosClient(load_settings().ecos_api_key)


def _read_cache(path: Path) -> list[dict] | None:
	"""캐시 파일이 있고 TABLE_CACHE_DAYS일이 지나지 않았으면 rows를 돌려준다. 없거나 깨졌으면 None."""
	try:
		data = json.loads(path.read_text(encoding="utf-8"))
	except (OSError, ValueError):
		return None
	if time.time() - data.get("saved_at", 0) > TABLE_CACHE_DAYS * 86400:
		return None
	return data.get("rows")


def _write_cache(path: Path, rows: list[dict]) -> None:
	"""rows를 캐시 파일로 저장한다. 저장에 실패해도 조회 결과는 그대로 쓴다."""
	try:
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(json.dumps({"saved_at": time.time(), "rows": rows}, ensure_ascii=False), encoding="utf-8")
	except OSError:
		logger.warning("ECOS 목록 캐시를 저장하지 못했습니다: %s", path)


def _fetch_all(fetch_page) -> list:
	"""fetch_page(start, end)를 PAGE_SIZE씩 불러 모두 모은다. INFO-200(데이터 없음)은 빈 목록으로 본다."""
	rows: list = []
	start = 1
	while True:
		try:
			page = fetch_page(start, start + PAGE_SIZE - 1)
		except EcosResponseError as e:
			if e.code == "INFO-200":
				break
			raise
		rows.extend(page)
		if len(page) < PAGE_SIZE:
			break
		start += PAGE_SIZE
	return rows


def load_table_list(client: EcosClient | None = None, refresh: bool = False) -> list[StatisticTable]:
	"""ECOS 통계표 목록(StatisticTableList) 전체. 조회할 수 있는 표(주기가 있는 표)만 돌려준다.

	data/ecos/statistic_tables.json 캐시가 TABLE_CACHE_DAYS일 안이면 API를 부르지 않는다. refresh=True면 새로 받는다.
	"""
	path = CACHE_DIR / "statistic_tables.json"
	rows = None if refresh else _read_cache(path)
	if rows is None:
		client = client or _client()
		tables = _fetch_all(lambda s, e: client.list_tables(start_num=s, end_num=e))
		rows = [asdict(t) for t in tables]
		_write_cache(path, rows)
	# 분류 제목 줄은 주기가 비어 있다 (예: '1. 통화 및 유동성') → 조회할 수 없으므로 뺀다
	return [StatisticTable(**r) for r in rows if r.get("cycle")]


def load_item_list(stat_code: str, client: EcosClient | None = None, refresh: bool = False) -> list[StatisticItem]:
	"""통계표 1개의 항목 목록(StatisticItemList). data/ecos/items_{stat_code}.json에 캐시한다."""
	path = CACHE_DIR / f"items_{stat_code}.json"
	rows = None if refresh else _read_cache(path)
	if rows is None:
		client = client or _client()
		items = _fetch_all(lambda s, e: client.list_items(stat_code, start_num=s, end_num=e))
		rows = [asdict(i) for i in items]
		_write_cache(path, rows)
	return [StatisticItem(**r) for r in rows]


# ---------------------------------------------------------------------
# 2) · 3) 검색어 만들기, 후보 통계표 고르기
# ---------------------------------------------------------------------
def period_to_cycle(period: str) -> str | None:
	"""Indicator.period 형식으로 ECOS 주기를 정한다 (2025→A, 2026Q2→Q, 202607→M, 20261023→D). 형식이 틀리면 None."""
	for pattern, cycle in _PERIOD_CYCLES:
		if pattern.match(period.strip().upper()):
			return cycle
	return None


def _key(text: str) -> str:
	"""비교용 열쇠: 공백을 모두 없애고 소문자로 바꾼다."""
	return re.sub(r"\s+", "", text or "").lower()


def _request_keywords(names: list[str]) -> dict[str, list[str]]:
	"""지표 이름 목록을 LLM에 보내 이름별 검색어를 받는다. 실패하면 빈 dict (이름 자체로만 검색한다)."""
	from hri.llm.llm_client import get_chat_model

	try:
		model = get_chat_model(temperature=0, max_tokens=FINDER_MAX_TOKENS).with_structured_output(_KeywordList)
		result = model.invoke([
			{"role": "system", "content": KEYWORD_SYSTEM_PROMPT},
			{"role": "user", "content": "[지표 이름]\n" + "\n".join(f"- {n}" for n in names)},
		])
	except Exception:
		logger.exception("ECOS 검색어 만들기 실패, 지표 이름으로만 검색합니다")
		return {}
	if result is None:
		return {}
	return {item.name.strip(): [k for k in item.keywords if k.strip()] for item in result.items}


def _rank_tables(keywords: list[str], cycle: str, tables: list[StatisticTable]) -> list[StatisticTable]:
	"""주기가 맞는 통계표를 검색어 점수 순으로 MAX_TABLES개 고른다. 점수 0인 표는 뺀다.

	점수: 표 이름에 들어 있는 검색어 글자 수의 합 (긴 검색어가 맞을수록 더 정확하다고 본다)
	"""
	keys = {_key(k) for k in keywords if len(_key(k)) >= 2}
	scored = []
	for table in tables:
		if table.cycle != cycle:
			continue
		name = _key(table.stat_name)
		score = sum(len(k) for k in keys if k in name)
		if score:
			scored.append((score, table))
	scored.sort(key=lambda pair: -pair[0])
	return [table for _, table in scored[:MAX_TABLES]]


def _in_range(period: str, item: StatisticItem) -> bool:
	"""지표 시점이 항목 수록 기간 안인지 (같은 주기면 글자 비교로 순서가 맞다). 수록 기간을 모르면 True."""
	if not item.start_time or not item.end_time:
		return True
	return item.start_time <= period <= item.end_time


def _pick_items(keywords: list[str], cycle: str, periods: list[str], items: list[StatisticItem]) -> list[StatisticItem]:
	"""통계표 항목 중 주기가 맞고 지표 시점이 하나라도 수록 기간 안에 드는 항목을 MAX_ITEMS_PER_TABLE개 고른다.

	검색어가 이름에 든 항목을 앞에 둔다. 두 번째 단계(Group2) 항목은 기간과 상관없이 남긴다 (첫 단계 항목과 함께 쓰므로).
	"""
	keys = [_key(k) for k in keywords if len(_key(k)) >= 2]
	usable = [
		i for i in items
		if i.cycle == cycle and (i.grp_code == "Group2" or any(_in_range(p, i) for p in periods))
	]
	usable.sort(key=lambda i: -sum(len(k) for k in keys if k in _key(i.item_name)))
	return usable[:MAX_ITEMS_PER_TABLE]


# ---------------------------------------------------------------------
# 4) · 5) 판별과 검사
# ---------------------------------------------------------------------
class _Group:
	"""이름·단위·주기가 같은 지표 묶음과 그 후보. LLM에는 [지표 no]로 보여 준다."""

	def __init__(self, no: int, name: str, unit: str, cycle: str, indicators: list[Indicator]):
		self.no, self.name, self.unit, self.cycle = no, name, unit, cycle
		self.indicators = indicators
		self.periods = sorted({ind.period.strip().upper() for ind in indicators})
		self.candidates: dict[str, tuple[StatisticTable, list[StatisticItem]]] = {}


def _format_groups(groups: list[_Group]) -> str:
	"""지표 묶음과 후보 통계표·항목을 LLM에 보여 줄 글로 만든다."""
	blocks = []
	for g in groups:
		values = ", ".join(f"{ind.period.strip().upper()} {ind.value}" for ind in g.indicators)
		lines = [f"[지표 {g.no}] {g.name} (단위 {g.unit}, 주기 {g.cycle}) 값: {values}", "[후보]"]
		for stat_code, (table, items) in g.candidates.items():
			lines.append(f"  [통계표 {stat_code}] {table.stat_name}")
			for i in items:
				lines.append(f"  - {i.grp_name or i.grp_code} · {i.item_code} {i.item_name} ({i.start_time}~{i.end_time})")
		blocks.append("\n".join(lines))
	return "\n\n".join(blocks)


def _request_choice(groups_text: str) -> _EcosPick | None:
	"""모든 지표와 후보를 LLM에 보내 가장 연관된 짝 하나를 고르게 한다. 실패하면 None."""
	from hri.llm.llm_client import get_chat_model

	try:
		model = get_chat_model(temperature=0, max_tokens=FINDER_MAX_TOKENS).with_structured_output(_EcosPick)
		return model.invoke([
			{"role": "system", "content": CHOICE_SYSTEM_PROMPT},
			{"role": "user", "content": groups_text},
		])
	except Exception:
		logger.exception("ECOS 항목 판별 실패")
		return None


def _find_item(items: list[StatisticItem], code: str | None, group: str | None = None) -> StatisticItem | None:
	"""항목 목록에서 코드가 같은 항목을 찾는다. group을 주면 그 단계(Group1·Group2)에서만 찾는다."""
	for item in items:
		if item.item_code == code and (group is None or item.grp_code == group):
			return item
	return None


def _to_query(choice: _EcosPick | None, groups: list[_Group], all_items: dict[str, list[StatisticItem]]) -> EcosQuery | None:
	"""LLM이 고른 결과를 검사해 EcosQuery로 만든다. 쓸 수 없으면 None.

	- usable=false, 없는 지표 번호, 그 지표의 후보에 없는 통계표·항목코드는 버린다 (LLM이 지어낸 코드 방지)
	- item_code2는 같은 통계표의 두 번째 단계 항목에 있을 때만 쓴다 (없으면 빼고 조회 → 하위 항목이 모두 조회됨)
	- 기간: 고른 지표의 시점 중 항목 수록 기간 안에 드는 가장 최근 시점 (없으면 None)
	"""
	if choice is None or not choice.usable:
		return None
	group = next((g for g in groups if g.no == choice.indicator_no), None)
	if group is None or choice.stat_code not in group.candidates:
		return None
	items = [i for i in all_items[choice.stat_code] if i.cycle == group.cycle]
	# 첫 번째 항목코드는 첫 단계(Group1)에서 찾는다. 단계 구분이 없는 표면 전체에서 찾는다
	has_groups = any(i.grp_code == "Group1" for i in items)
	item = _find_item(items, choice.item_code, "Group1" if has_groups else None)
	if item is None:
		return None
	periods = [p for p in group.periods if _in_range(p, item)]
	if not periods:
		return None
	item_code2 = choice.item_code2 if _find_item(items, choice.item_code2, "Group2") else None
	return EcosQuery(
		stat_code=choice.stat_code, cycle=group.cycle, item_code=item.item_code, item_code2=item_code2,
		start=periods[-1], end=periods[-1], reason=choice.reason, indicator_name=group.name,
	)


def find_usable_data(indicators: list[Indicator], client: EcosClient | None = None) -> EcosQuery | None:
	"""
	지표 목록에서 ECOS로 확인하기에 가장 연관된 지표 1개와 통계 1개를 골라 조회 조건(EcosQuery)을 만든다.

	입력
		indicators: extract_indicators()가 돌려준 지표 목록 (이름은 대표 이름으로 통일된 상태)
		client: 재사용할 EcosClient (None이면 새로 만든다)

	반환
		EcosQuery 1개 (indicator_name=고른 지표 이름, start=end=그 지표의 시점, reason=고른 이유).
		비교할 통계가 없거나 지표 시점이 모두 ECOS 수록 기간 밖(예: 전망치)이면 None

	예외
		ExternalApiError: ECOS 통계표 목록을 불러오지 못했을 때 (항목 목록 1개 실패는 그 통계표만 건너뛴다)

	사용하는 곳: indicator_view.on_compare() (보고서 수치와 ECOS 수치 비교)
	"""
	# 기준 시점 형식이 맞는 지표만, 이름·단위·주기가 같은 것끼리 묶는다
	grouped: dict[tuple[str, str, str], list[Indicator]] = {}
	for ind in indicators:
		cycle = period_to_cycle(ind.period)
		if cycle:
			grouped.setdefault((ind.name.strip(), ind.unit.strip(), cycle), []).append(ind)
	if not grouped:
		return None

	client = client or _client()
	try:
		tables = load_table_list(client)
	except Exception as e:
		raise ExternalApiError(f"ECOS 통계표 목록을 불러오지 못했습니다. ({e})") from e

	# 2) 검색어: 지표 이름마다 LLM 검색어 + 이름 자체
	llm_keywords = _request_keywords(sorted({name for name, _, _ in grouped}))

	# 3) 지표 묶음마다 후보 모으기 (후보가 없는 묶음은 LLM에 보여 주지 않는다)
	groups: list[_Group] = []
	all_items: dict[str, list[StatisticItem]] = {}
	for (name, unit, cycle), members in grouped.items():
		group = _Group(len(groups) + 1, name, unit, cycle, members)
		keywords = [name, *llm_keywords.get(name, [])]
		for table in _rank_tables(keywords, cycle, tables):
			if table.stat_code not in all_items:
				try:
					all_items[table.stat_code] = load_item_list(table.stat_code, client)
				except Exception:
					logger.exception("ECOS 항목 목록 조회 실패: %s", table.stat_code)
					all_items[table.stat_code] = []
			items = _pick_items(keywords, cycle, group.periods, all_items[table.stat_code])
			if any(i.grp_code != "Group2" for i in items):
				group.candidates[table.stat_code] = (table, items)
		if group.candidates:
			groups.append(group)
		else:
			logger.info("ECOS 후보 통계표 없음: %s (%s)", name, cycle)
	if not groups:
		return None

	# 4) · 5) 판별·검사
	choice = _request_choice(_format_groups(groups))
	query = _to_query(choice, groups, all_items)
	if query is None:
		logger.info("ECOS 비교 통계 없음: %s", choice.reason if choice else "판별 실패")
	return query
