"""한국수출입은행 환율 클라이언트(exchange_client.py) 테스트 · 네트워크 없이 가짜 응답으로 확인 (류현수)"""

import httpx
import pytest

from hri.api import exchange_client

USD_ROW = {"result": 1, "cur_unit": "USD", "deal_bas_r": "1,385.5"}


@pytest.fixture
def fake_api(monkeypatch):
	"""실제 서버 대신 bodies에 넣은 JSON을 차례로 돌려주고, 나간 요청은 sent에 기록"""
	bodies, sent = [], []
	real_client = httpx.Client

	def handler(request):
		sent.append(request)
		return httpx.Response(200, json=bodies.pop(0))

	monkeypatch.setattr(
		exchange_client.httpx, "Client",
		lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
	)
	return bodies, sent


# [확인 1] "1,385.5" → 1385.5
def test_returns_rate(fake_api):
	bodies, _ = fake_api
	bodies.append([USD_ROW])
	assert exchange_client.get_exchange_rate("key") == 1385.5


# [확인 2] 빈 리스트(주말·공휴일)면 전날로 거슬러 올라감
def test_falls_back_to_previous_day(fake_api):
	bodies, sent = fake_api
	bodies.extend([[], [], [USD_ROW]])
	assert exchange_client.get_exchange_rate("key") == 1385.5
	assert len(sent) == 3


# [확인 3] 오늘 + max_back_days일 전까지 모두 비면 LookupError
def test_gives_up(fake_api):
	bodies, sent = fake_api
	bodies.extend([[]] * 4)
	with pytest.raises(LookupError):
		exchange_client.get_exchange_rate("key", max_back_days=3)
	assert len(sent) == 4


# [확인 4] result가 1이 아니면(3=인증키 오류) RuntimeError
def test_api_error_code(fake_api):
	bodies, _ = fake_api
	bodies.append([{"result": 3}])
	with pytest.raises(RuntimeError):
		exchange_client.get_exchange_rate("key")
