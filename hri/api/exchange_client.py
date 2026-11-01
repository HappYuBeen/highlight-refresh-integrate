"""한국수출입은행 실시간 환율(토큰 비용 원화 환산용) · get_exchange_rate (류현수)"""
from datetime import date, timedelta

import httpx

from hri.config.settings import load_settings

BASE_URL = "https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON"


def get_exchange_rate(authkey: str, currency: str = "USD", max_back_days: int = 7) -> float:
    """매매기준율 반환. 주말·공휴일·11시 이전엔 데이터가 없어 전날로 거슬러 올라감. 연휴가 있다면 2일 이상 데이터가 없을 수 있음. 기본값을 7일로 설정함"""
    with httpx.Client(timeout=10.0, follow_redirects=True) as client:
        for back in range(max_back_days+1):
            day = (date.today() - timedelta(days=back)).strftime("%Y%m%d")
            # AP01 : 환율, AP02 : 대출금리, AP03 : 국제금리
            response = client.get(BASE_URL, params={"authkey": authkey, "data": "AP01", "searchdate": day})
            if response.is_error:
                raise RuntimeError(f"수출입은행 API HTTP 오류: {response.status_code}")
            rows = response.json()
            if rows and rows[0].get("result") != 1:  # 1 : 성공, 2 : DATA코드 오류, 3 : 인증코드 오류, 4 : 일일제한횟수 마감
                raise RuntimeError(f"수출입은행 API 오류 코드: {rows[0].get('result')}")
            for row in rows:
                if row.get("cur_unit") == currency:
                    return float(row["deal_bas_r"].replace(",", ""))
    raise LookupError(f"최근 {max_back_days}일간 {currency} 환율을 찾지 못했습니다")


if __name__ == "__main__":
    print(get_exchange_rate(load_settings().koreaexim_api_key))
