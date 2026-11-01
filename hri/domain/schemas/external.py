"""외부 데이터 스키마 · Indicator, EcosChoice, EcosQuery, TermCard, EcosExchangeRate (엄정현), EximExchangeRate (류현수)"""


from typing import Literal

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

__all__ = ["Indicator", "EcosChoice", "EcosQuery", "TermCard", "EcosExchangeRate", "EximExchangeRate"]

# [지표정보] 보고서에서 뽑은 지표 1개 (indicators 테이블 1행). LLM용 스키마에서는 report_id를 빼고 저장할 때 코드가 붙인다
class Indicator(BaseModel):
	"""보고서 본문·표·차트에 적힌 경제 지표 수치 1개.
	예: '2분기 경제성장률(전기비)은 0.6%' → name='경제성장률', value=0.6, unit='%', period='2026Q2'
	"""
	# 보고서 번호: LLM 설명(JSON 스키마)에서 빠지므로 LLM은 채우지 않고, 저장할 때 코드가 넣는다
	report_id: SkipJsonSchema[int | None] = None
	# 지표 이름: 보고서에 쓰인 이름 그대로 받고, 이름 통일(INDICATOR_ALIASES)은 코드가 나중에 한다
	name: str = Field(description="보고서에 쓰인 지표 이름 그대로 (예: 경제성장률, 소비자물가 상승률, 실업률)")
	# 지표 값: 단위·기호 없이 숫자만 받는다 (글자 "0.6"이 와도 Pydantic이 0.6으로 바꾼다)
	value: float = Field(description="보고서에 적힌 수치. 숫자만 쓴다 (예: 0.6, -1.6, 1324.6)")
	# 단위: 값 옆에 붙어 있던 단위를 따로 적는다
	unit: str = Field(description="수치의 단위 (예: %, %p, 원, 억 원, 조 원)")
	# 기준 시점: ECOS 조회 기간(start·end)을 만들 수 있게 형식을 정해 둔다
	period: str = Field(description="수치의 기준 시점. 연 YYYY, 분기 YYYYQn, 월 YYYYMM, 일 YYYYMMDD 중 하나 (예: 2025, 2026Q2, 202607)")
	# 출처 위치: 본문·표·차트 셋 중 하나만 받는다
	source: Literal["text", "table", "chart"] = Field(default="text", description="수치를 찾은 위치 (본문 text / 표 table / 차트 chart)")
	# 출처 쪽: 수치가 적힌 쪽 번호. 표·차트는 블록 머리글의 쪽, 본문은 속한 섹션의 시작 쪽이라 본문은 정확한 쪽이 아닐 수 있다
	page: int | None = Field(default=None, description="수치가 적힌 쪽 번호 (1부터). 표·차트는 '[표 p.12 ...]'의 12, 본문은 속한 섹션 줄 '## 제목 (p.3-5)'의 시작 쪽 3. 알 수 없으면 null")
	# 사람 확인 필요 여부: 차트에서 읽은 값처럼 정확하지 않을 수 있으면 True
	needs_review: bool = Field(default=False, description="수치가 정확한지 사람이 확인해야 하면 true (예: 차트에서 눈으로 읽은 값)")

# 개발자 메모: name은 str로 둔다. domain이 api.ecos_finder를 import하면 순환 import가 생기기 때문이다.
# 후보(INDICATOR_NAMES)에 있는 이름인지는 find_usable_data()가 코드로 검사한다.
class EcosChoice(BaseModel):
	"""보고서 수치와 비교하려고 한국은행(ECOS)에서 조회할 지표를, 주어진 후보 목록에서 하나 고른 결과."""

	# 고른 지표 이름: 후보 목록에 있는 이름을 글자 그대로 옮겨 적어야 코드가 통계 코드를 찾을 수 있다
	name: str = Field(description="후보 목록에 있는 지표 이름 하나를 글자 그대로 (예: 경제성장률(분기, 전기비))")
	# 고른 이유: 화면·기록에 보여 줄 짧은 설명
	reason: str = Field(description="이 지표를 고른 이유 한 문장")


# 개발자 메모: LLM이 아니라 코드가 채운다. 코드는 INDICATOR_CODES, 기간은 Indicator.period에서 가져온다.
class EcosQuery(BaseModel):
	"""한국은행(ECOS) OpenAPI StatisticSearch를 호출할 때 쓰는 조회 조건."""

	# 통계표 코드 (예: 200Y102)
	stat_code: str = Field(description="ECOS 통계표 코드 (예: 200Y102)")
	# 주기: 연·분기·월·일 네 가지만 받는다
	cycle: Literal["A", "Q", "M", "D"] = Field(description="A(연) · Q(분기) · M(월) · D(일)")
	# 첫 번째 항목코드 (예: 국내총생산 10111)
	item_code: str = Field(description="통계표 안의 첫 번째 항목코드 (예: 10111)")
	# 두 번째 항목코드: 실업률처럼 항목이 두 단계인 통계표에만 쓴다
	item_code2: str | None = Field(
		# 기본값은 None(값 없음)
		default=None,
		# 비워 두면 원계열·계절조정처럼 하위 항목이 모두 함께 조회된다
		description="두 번째 항목코드 (예: 실업률 원계열 I28A). None이면 하위 항목이 모두 조회된다",
	)
	# 조회 시작 시점: cycle과 같은 형식이어야 ECOS가 값을 돌려준다
	start: str = Field(description="조회 시작 시점. cycle 형식과 같아야 함 (A: 2025, Q: 2026Q1, M: 202607, D: 20261023)")
	# 조회 끝 시점: start와 같은 형식
	end: str = Field(description="조회 끝 시점. start와 같은 형식")
	# 조회 이유: EcosChoice.reason을 그대로 옮긴다
	reason: str = Field(default="", description="이 데이터를 조회하는 이유")
	# 비교할 보고서 지표 이름: fetch_indicators(query, query.indicator_name)에 넘긴다 (류현수)
	indicator_name: str = Field(default="", description="이 데이터와 비교할 보고서 지표 이름 (Indicator.name)")


class TermCard(BaseModel):
	"""경제 용어 1개의 원문 정의와, 그 정의만 근거로 페르소나 난이도에 맞춰 쉽게 다시 쓴 설명."""
	
	term: str = Field(description="설명할 경제 용어 단어")
	# 원문 정의: terms 캐시 → ECOS 통계용어 → 표준국어대사전 순서로 찾아온 문장 그대로
	definition: str = Field(description="찾아온 원문 정의 그대로 (terms 캐시 · ECOS 통계용어 · 표준국어대사전)")
	# 쉬운 설명: 원문에 없는 내용은 넣지 않는다
	explanation: str = Field(description="원문 정의에 있는 내용만으로 페르소나(입문자·실무자·전문가) 난이도에 맞춰 다시 쓴 2~3문장")
	source: str = Field(description="원문 정의의 출처 (예: ECOS 통계용어 / 표준국어대사전)")


class EcosExchangeRate(BaseModel):
	"""한국은행(ECOS) 일별 매매기준율 1건. 보고서 문서 속 환율과 비교할 때 쓴다."""
	# 통화: 세 가지만 받는다 (ECOS 항목코드 USD 0000001 · JPY 0000002 · EUR 0000003)
	currency: Literal["USD", "JPY", "EUR"] = Field(description="통화 코드 USD · JPY · EUR")
	value: float = Field(description="1통화당 원화 금액. JPY는 100엔당 원화 (예: USD 1324.6, JPY 846.12)")
	base_date: str = Field(description="기준일 YYYYMMDD")


class EximExchangeRate(BaseModel):
    """[환율 정보] 한국수출입은행 OpenAPI 환율 응답 1건."""

    RESULT: int = Field(description="조회 결과 (1: 성공, 2: DATA 코드 오류, 3: 인증 코드 오류, 4: 일일 제한 횟수 초과)")
    CUR_UNIT: str | None = Field(default=None, description="통화 코드 (예: USD, JPY(100엔), EUR)")
    CUR_NM: str | None = Field(default=None, description="국가/통화명")
    TTB: str | None = Field(default=None, description="전신환(송금) 받으실 때")
    TTS: str | None = Field(default=None, description="전신환(송금) 보내실 때")
    DEAL_BAS_R: str | None = Field(default=None, description="매매 기준율")
    BKPR: str | None = Field(default=None, description="장부가격")
    YY_EFEE_R: str | None = Field(default=None, description="년환가료율")
    TEN_DD_EFEE_R: str | None = Field(default=None, description="10일환가료율")
    KFTC_DEAL_BAS_R: str | None = Field(default=None, description="서울외국환중개 매매기준율")
    KFTC_BKPR: str | None = Field(default=None, description="서울외국환중개 장부가격")
