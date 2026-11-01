"""공통 LLM 모듈 · get_chat_model (서동안), cost_korean_won, get_usd_krw (임세준)

- get_chat_model(): ChatOpenAI 모델을 만들어 반환 (서동안)
- cost_korean_won(): 토큰 사용량을 원화 비용으로 환산 (임세준)
- get_usd_krw(): 원/달러 환율 조회, 실패하면 1,400원 (임세준)
"""

import json

from langchain_core.messages.ai import UsageMetadata
from langchain_openai import ChatOpenAI

from hri.config.settings import load_settings

# 환율을 가져오지 못했을 때 쓰는 기본 원/달러 환율
DEFAULT_USD_KRW = 1400.0

"""기본 모델은 hri.Settings.llm_model에서 가져온다. 모델을 바꿀 때는 Settings(.env)만 수정한다."""
MODEL = load_settings().llm_model


def get_chat_model(
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 4000,
	reasoning_effort: str | None = "none",
) -> ChatOpenAI:
    """ChatOpenAI 모델을 만들어 반환한다."""
    return ChatOpenAI(
        model=model or MODEL,
        temperature=temperature,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        stream_usage=True,
    )


def ask_llm(
    messages: list[dict],
    temperature: float = 0.0,
    max_tokens: int = 4000,
) -> tuple[str, UsageMetadata]:
    """자유 텍스트 응답용. 답변 텍스트와 usage_metadata를 반환한다."""
    response = get_chat_model(temperature=temperature, max_tokens=max_tokens).invoke(messages)
    return response.content, response.usage_metadata


def chat_bot(question: str) -> str:
    """잡담 응대 (qa_graph의 chat 노드: route_question이 'chat'으로 분류한 경우).

    검색 없이 1~2문장으로 친절히 답하고, 보고서 질문이면 보고서를 선택해 질문하도록 안내한다.
    """
    messages = [
        {
            "role": "system",
            "content": (
                "당신은 경제 분석 보고서 요약 서비스의 안내 도우미입니다. "
                "인사·감사·잡담에는 검색 없이 1~2문장으로 친절하게 답합니다. "
                "경제 보고서 내용에 대한 질문이면 답하지 말고, "
                "화면에서 보고서를 선택한 뒤 질문해 달라고 안내합니다."
            ),
        },
        {"role": "user", "content": question},
    ]
    answer, _ = ask_llm(messages)
    return answer


def cost_korean_won(usage: dict[str, UsageMetadata], usd_to_krw: float) -> float:
	"""
	토큰 사용량을 원화 비용으로 바꾸기

	usage는 그래프를 실행하는 동안 UsageMetadataCallbackHandler가 모은 모델별 토큰 수이다.
	예) {"gpt-5-mini-2025-08-07": {"input_tokens": 1200, "output_tokens": 300, "total_tokens": 1500}}

	계산 방법 (모델마다 계산해서 모두 더함)
	- 달러 = 입력 토큰 / 1,000,000 × 입력 단가 + 출력 토큰 / 1,000,000 × 출력 단가
	- 원화 = 달러 × usd_to_krw(원/달러 환율)
	단가는 .env의 MODEL_PRICES(1M 토큰당 USD)에서 읽는다.

	- OpenAI는 모델 이름 뒤에 날짜를 붙여서 알려 준다 ("gpt-5-mini-2025-08-07").
	  그래서 모델 이름의 앞부분이 단가표 이름("gpt-5-mini")과 같은지 보고 단가를 찾는다.
	- 단가표에 없는 모델은 0원으로 계산한다.
	- DB에 저장하지 않고 계산만 하는 함수다 (저장은 insert_usage, save_qa_log가 함)

	입력: usage (모델별 토큰 수 딕셔너리), usd_to_krw (원/달러 환율, get_usd_krw()로 받은 값)
	출력: float (원화 비용, 소수점 넷째 자리까지)
	"""
	# 모델별 단가표 (.env에 글자로 저장돼 있어서 딕셔너리로 바꿈)
	settings = load_settings()
	prices = json.loads(settings.model_prices)

	total_won = 0.0

	for model_name, tokens in usage.items():
		input_tokens = tokens["input_tokens"]
		output_tokens = tokens["output_tokens"]

		# 단가표에서 이 모델의 단가 찾기
		# 모델 이름 앞부분을 단가표 이름 길이만큼 잘라서 같은지 비교
		price = None
		for price_name, model_price in prices.items():
			if model_name[0:len(price_name)] == price_name:
				price = model_price

		# 단가가 없으면 0원으로 하고 다음 모델로 넘어감
		if price == None:
			continue

		# 1M 토큰당 USD 단가로 달러 계산
		dollar = input_tokens / 1000000 * price["input"] + output_tokens / 1000000 * price["output"]

		# 달러를 원화로 바꿔서 더하기
		total_won = total_won + dollar * usd_to_krw

	return round(total_won, 4)


def get_usd_krw() -> float:
	"""
	원/달러 환율 가져오기 (토큰 비용 계산용)

	아래 순서로 환율을 가져오고, 먼저 성공한 값을 쓴다.
	1. 한국수출입은행 API (get_exchange_rate) - 매매기준율, 그날 값이 없으면 전날로 거슬러 올라감
	2. 한국은행 ECOS API (get_exchange_rates) - 하루 단위 기준 환율, 주말·공휴일이면 최근 영업일 값
	3. 둘 다 실패하면 기본 환율 1,400원

	- 토큰 비용은 실시간에 가까운 한국수출입은행 환율을 먼저 쓴다.
	- 캐시를 쓰지 않아서 부를 때마다 새로 가져온다.
	  그래서 함수 안에서 불러오고, 없거나 에러가 나면 다음 순서로 넘어간다.

	입력: 없음
	출력: float (원/달러 환율)
	"""
	# 1) 한국수출입은행 환율 (API 키를 같이 넘겨야 함)
	try:
		from hri.api.exchange_client import get_exchange_rate

		settings = load_settings()
		usd_krw = get_exchange_rate(settings.koreaexim_api_key, "USD")
		return usd_krw
	except Exception:
		pass

	# 2) 한국은행 ECOS 환율 (통화 목록을 넘기면 통화별 환율 리스트를 돌려줌)
	try:
		from hri.api.ecos_client import get_exchange_rates

		rates = get_exchange_rates(["USD"])
		usd_krw = rates[0].value
		return usd_krw
	except Exception:
		pass

	# 3) 둘 다 실패하면 기본 환율
	return DEFAULT_USD_KRW