"""
외부 데이터 스키마 테스트 (엄정현)
외부 데이터 스키마(external.py)가 잘못된 값을 막는지 확인하는 테스트 (네트워크·DB 없이 실행).
"""

# pytest: 테스트를 실행하고, "이 코드에서 오류가 나야 한다"를 검사하는 도구(pytest.raises)를 준다
import pytest
# ValidationError: Pydantic이 값이 틀렸을 때 내는 오류의 종류. 이 오류가 나는지를 검사한다
from pydantic import ValidationError

# 검사할 스키마 두 개(지표 1개를 담는 Indicator, ECOS 조회 조건을 담는 EcosQuery)를 불러온다
from hri.domain.schemas.external import EcosQuery, Indicator


# [확인 1] Indicator의 source에 허용되지 않은 값을 넣으면 오류가 나야 한다
def test_indicator_source_rejects_unknown_value():
	# 아래 블록 안에서 ValidationError가 나면 통과, 안 나면 테스트 실패로 처리한다
	with pytest.raises(ValidationError):
		# source는 "text"·"table"·"chart"만 받으므로, "pdf"를 일부러 넣어 오류를 일으킨다
		Indicator(report_id=1, name="경제성장률", value=0.6, unit="%", period="2026Q2", source="pdf")


# [확인 2] Indicator의 value에 숫자 모양 글자("0.6")를 넣으면 실수(float)로 바뀌어야 한다
def test_indicator_value_converted_to_float():
	# value를 글자 "0.6"으로 넣어 Indicator를 하나 만든다
	ind = Indicator(report_id=1, name="경제성장률", value="0.6", unit="%", period="2026Q2")
	# 저장된 value가 실수(float) 종류인지 확인한다. 아니면 테스트 실패
	assert isinstance(ind.value, float)
	# 값도 0.6 그대로인지 확인한다. 다르면 테스트 실패
	assert ind.value == 0.6


# [확인 3] EcosQuery를 만들 때 item_code2를 안 넣으면 None(값 없음)이 들어가야 한다
def test_ecos_query_item_code2_default_none():
	# item_code2를 빼고 EcosQuery를 만든다 (분기 경제성장률 조회 조건)
	q = EcosQuery(stat_code="200Y102", cycle="Q", item_code="10111", start="2026Q1", end="2026Q2")
	# item_code2가 None인지 확인한다. 다른 값이면 테스트 실패
	assert q.item_code2 is None


# [확인 4] EcosQuery에 꼭 필요한 값(start)을 빠뜨리면 오류가 나야 한다
def test_ecos_query_requires_start():
	# 아래 블록 안에서 ValidationError가 나면 통과, 안 나면 테스트 실패로 처리한다
	with pytest.raises(ValidationError):
		# 조회 시작 시점(start)을 일부러 빼고 EcosQuery를 만들어 오류를 일으킨다
		EcosQuery(stat_code="200Y102", cycle="Q", item_code="10111", end="2026Q2")