"""대시보드 스키마 · DashboardData (임세준)"""

from typing import Optional

from pydantic import BaseModel

__all__ = ["DashboardData"]


class DashboardData(BaseModel):
	"""
	관리자 대시보드에 보여줄 데이터

	[1차] get_dashboard_data()가 채움
	top_questions: 질문 TOP 10 (마크다운 표)
	source_ratio: 자료 유무 응답 비율
	avg_response_ms: 평균 응답 시간 (ms)
	usage_by_flow: flow별 토큰 사용량 (마크다운 표)
	cost_krw: 원화 비용 합계
	reports: 보고서 목록 (마크다운 표)
	recent_errors: 최근 에러 로그 (마크다운 표)

	[2차] get_dashboard_data_v2()가 채움 (1차만 만들 때는 None)
	daily_chart: 일별 질문 수·평균 응답 시간 추이 차트 PNG 경로 (기록이 없으면 None)
	persona_ratio: 페르소나별 사용 비율 (마크다운 표)
	route_ratio: 질문 처리 방식(보고서 근거 답변·자료에 없음·잡담·오류)별 비율 (마크다운 표)
	feedback_ratio: 👍/👎 피드백 비율 (마크다운 표)
	no_source_questions: 근거 없는 질문 목록 (마크다운 표)
	"""

	top_questions: str
	source_ratio: float
	avg_response_ms: float
	usage_by_flow: str
	cost_krw: float
	reports: str
	recent_errors: str

	daily_chart: Optional[str] = None
	persona_ratio: Optional[str] = None
	route_ratio: Optional[str] = None
	feedback_ratio: Optional[str] = None
	no_source_questions: Optional[str] = None