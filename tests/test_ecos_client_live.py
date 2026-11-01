"""ECOS 클라이언트(ecos_client.py) 실제 API 테스트 (류현수)

실행: pytest tests/test_ecos_client_live.py -v
"""

import os
from datetime import date

import pytest
from dotenv import load_dotenv
from ecos import EcosClient
from ecos.exceptions import EcosResponseError

from hri.api import ecos_client
from hri.domain.schemas.external import EcosExchangeRate, EcosQuery, Indicator, TermCard

load_dotenv()
API_KEY = os.getenv("ECOS_API_KEY", "")

pytestmark = pytest.mark.skipif(not API_KEY, reason="ECOS_API_KEY가 없어 실제 API 테스트를 건너뜀")

# 분기 경제성장률(실질 GDP 전기대비) · tests/test_schemas.py와 같은 조회 조건
GDP_QUERY = EcosQuery(stat_code="200Y102", cycle="Q", item_code="10111", start="2025Q1", end="2025Q4")


@pytest.fixture(scope="module")
def client():
	"""모듈 전체에서 클라이언트 1개를 재사용하고 끝나면 연결을 닫는다"""
	with EcosClient(API_KEY) as c:
		yield c


# [확인 1] EcosQuery로 조회하면 해당 통계표·기간의 행이 돌아온다
def test_fetch_rows(client):
	rows = ecos_client.fetch_rows(GDP_QUERY, client)
	assert rows, "2025년 분기 경제성장률이 비어 있음"
	assert all(r.stat_code == GDP_QUERY.stat_code for r in rows)
	assert all(r.time.startswith("2025Q") for r in rows)


# [확인 2] StatisticRow → Indicator 변환: value는 float, period는 '2025Q1' 형식
def test_fetch_indicators(client):
	indicators = ecos_client.fetch_indicators(GDP_QUERY, "경제성장률", client)
	assert indicators
	for ind in indicators:
		assert isinstance(ind, Indicator)
		assert ind.name == "경제성장률"
		assert isinstance(ind.value, float)
		assert ind.period.startswith("2025Q")
		assert ind.unit


# [확인 3] 데이터가 없는 기간(INFO-200)은 예외 대신 빈 리스트
def test_no_data_returns_empty(client):
	future = GDP_QUERY.model_copy(update={"start": "2099Q1", "end": "2099Q4"})
	assert ecos_client.fetch_rows(future, client) == []


# [확인 4] 잘못된 인증키는 INFO-200이 아니므로 그대로 예외
def test_invalid_key_raises():
	with EcosClient("invalid-key-for-test") as bad:
		with pytest.raises(EcosResponseError) as exc:
			ecos_client.fetch_rows(GDP_QUERY, bad)
	assert exc.value.code != "INFO-200"


# [확인 5] CURRENCY_ITEM_CODES의 항목코드가 실제 731Y001 항목명과 맞는지
@pytest.mark.parametrize("currency, keyword", [("USD", "미국달러"), ("JPY", "일본엔"), ("EUR", "유로")])
def test_currency_item_codes(client, currency, keyword):
	items = {i.item_code: i.item_name for i in client.list_items(ecos_client.EXCHANGE_STAT_CODE, end_num=1000)}
	code = ecos_client.CURRENCY_ITEM_CODES[currency]
	assert code in items, f"{currency} 항목코드 {code}가 731Y001에 없음"
	assert keyword in items[code], f"{code}의 항목명이 '{items[code]}'임"


# [확인 6] 평일(2026-10-23 금)은 그날 환율이 그대로 온다
def test_exchange_rates_weekday(client):
	rates = ecos_client.get_exchange_rates(base_date=date(2026, 10, 23), client=client)
	assert [r.currency for r in rates] == list(ecos_client.CURRENCY_ITEM_CODES)
	for r in rates:
		assert isinstance(r, EcosExchangeRate)
		assert r.base_date == "20261023"
	usd = next(r for r in rates if r.currency == "USD")
	assert 800 < usd.value < 2500, f"USD 환율이 비정상: {usd.value}"


# [확인 7] 주말(2026-10-24 토)을 넣으면 직전 영업일(2026-10-23 금) 값으로 거슬러 간다
def test_exchange_rates_weekend_falls_back(client):
	rates = ecos_client.get_exchange_rates(["USD"], base_date=date(2026, 10, 24), client=client)
	assert len(rates) == 1, "주말 날짜로 조회했더니 환율이 비어 있음"
	assert rates[0].base_date == "20261023"


# [확인 7-2] 거슬러 갈 범위 안에 영업일이 없으면(토·일만 조회) 해당 통화는 결과에서 빠진다
def test_exchange_rates_no_business_day(client):
	rates = ecos_client.get_exchange_rates(["USD"], base_date=date(2026, 10, 25), max_back_days=1, client=client)
	assert rates == []


# [확인 8] 통계용어 조회 결과로 TermCard를 만들 수 있다
def test_lookup_term(client):
	d = ecos_client.lookup_term("GDP", client)
	assert d is not None
	card = TermCard(**d, explanation="테스트용 설명")
	assert card.definition
	assert card.source == "ECOS 통계용어"
