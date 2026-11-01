""" external 스키마에 맞춰 ECOS API를 연결(류현수)  """

from datetime import date, timedelta

from ecos import Cycle, EcosClient
from ecos.exceptions import EcosResponseError
from ecos.models import StatisticRow

from hri.config.settings import load_settings
from hri.domain.schemas.external import EcosExchangeRate, EcosQuery, Indicator

EXCHANGE_STAT_CODE = "731Y001"
CURRENCY_ITEM_CODES = {"USD": "0000001", "JPY": "0000002", "EUR": "0000003"}

# 모듈 내에서 사용되는 client 반환 함수
# 이후 사용할 때 client를 여러번 재생성하지 않고, 각 함수에서 파라미터로 전달받아 재활용
def _client() -> EcosClient:
	return EcosClient(load_settings().ecos_api_key)

# schemas/external에 정의된 EcosQuery를 라이브러리 호출로 연결
def fetch_rows(query: EcosQuery, client: EcosClient | None = None, end_num: int = 100) -> list[StatisticRow]:
	"""EcosQuery를 기반으로 api 호출. INFO-200는 데이터가 없음을 의미, 빈 리스트 반환"""
	client = client or _client()
	try:
		return client.search(
			query.stat_code,
			Cycle(query.cycle),
			query.start,
			query.end,
			item_code1=query.item_code,
			item_code2=query.item_code2 or "?", # ?는 ecos 라이브러리에서 사용하는 와일드카드
			end_num = end_num,
		)
	except EcosResponseError as e:
		if e.code == "INFO-200":
			return []
		raise

def _to_float(raw: str) -> float | None:
	"""ECOS의 반환 값은 문자열이기 때문에, float 형으로 변환함."""
	try:
		return float(raw.replace(",", ""))
	except(AttributeError, ValueError):
		return None

# schemas/external에 정의된 Indicator를 StatisticRow와 연결함
def to_indicators(rows: list[StatisticRow], name: str) -> list[Indicator]:
	"""name은 비교할 보고서 지표 이름(Indicator.name)임. row.time 형식(2026Q2·202607·2025)은 Indicator.period와 동일함."""
	out = []
	for r in rows:
		value = _to_float(r.data_value)
		if value is None:
			continue
		out.append(Indicator(name=name, value=value, unit=r.unit_name, period=r.time, source="table", needs_review=False))
	return out

def fetch_indicators(query: EcosQuery, name: str, client: EcosClient | None = None) -> list[Indicator]:
	return to_indicators(fetch_rows(query, client), name)

# schemas/external에 정의된 EcosExchangeRate와 환율 정보를 연결
def get_exchange_rates(
	currencies: list[str] = list(CURRENCY_ITEM_CODES),
	base_date: date | None = None,
	max_back_days: int = 7,
	client: EcosClient | None = None,
) -> list[EcosExchangeRate]:
	"""통화별 환율. base_date(기본 오늘)가 주말·공휴일이면 max_back_days일 전까지 거슬러 가장 최근 영업일 값을 쓴다.
	기간 전체를 한 번에 조회한 뒤 가장 늦은 날짜의 행을 고르므로 통화당 요청은 1번이다.
	연휴가 max_back_days 이상인 경우 환율 조회가 불가능 할 수 있다."""
	client = client or _client()
	end = base_date or date.today()
	start = end - timedelta(days=max_back_days)
	result = []
	for cur in currencies:
		q = EcosQuery(
			stat_code=EXCHANGE_STAT_CODE, cycle="D", item_code=CURRENCY_ITEM_CODES[cur],
			start=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"),
		)
		rows = [r for r in fetch_rows(q, client) if _to_float(r.data_value) is not None]
		if rows:
			latest = max(rows, key=lambda r: r.time)
			result.append(EcosExchangeRate(currency=cur, value=_to_float(latest.data_value), base_date=latest.time))
	return result

# schemas/external에 정의된 TermCard와 통계 용어를 연결
def lookup_term(word: str, client: EcosClient | None = None) -> dict | None:
	"""TermCard의 term/definition/source만 채운 dict. TermCard(**d, explanation=...)로 완성."""
	client = client or _client()
	try:
		words = client.search_word(word)
	except EcosResponseError as e:
		if e.code == "INFO-200":
			return None
		raise
	if not words:
		return None
	hit = next((w for w in words if w.word.strip() == word), words[0])
	return {"term": hit.word, "definition": hit.content.strip(), "source": "ECOS 통계용어"}