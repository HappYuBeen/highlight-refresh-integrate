"""요약 스키마 · SectionSummary, ReportSummary, ThreeLineSummary, VerifyResult, MergedReport (김희진)"""
from typing import Literal
from datetime import datetime
from pydantic import BaseModel, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

__all__: list[str] = ["SectionSummary", "ReportSummary", "ThreeLineSummary", "VerifyResult", "MergedReport"]

# 섹션별 요약 - summarize_sections()의 결과
class SectionSummary(BaseModel):
    """경제 보고서 섹션 1개의 요약"""
    # AI가 채우는 값
    title: str = Field(description="섹션 제목, 원문 제목 그대로")
    summary: str = Field(description="섹션 핵심 내용 2~3문장 이내로 요약. 수치, 단위는 원문 그대로 쓰고 자료에 없는 내용은 쓰지 않음.")

    # 코드로 채우는 값(AI에게는 보이지 않음) / SkipJsonSchema = 해당 필드는 AI에게 보내는 양식에서 숨김
    section_id: SkipJsonSchema[int | None] = None
    start_page: SkipJsonSchema[int | None] = Field(default=None, ge=1)
    end_page: SkipJsonSchema[int | None] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def check_page(self):
        if self.start_page is not None and self.end_page is not None and self.end_page < self.start_page:
            raise ValueError("end_page는 start_page보다 작을 수 없습니다.")
        return self

# 보고서 전체 요약 - summarize_sections()의 결과 (summary_json에 저장)
class ReportSummary(BaseModel):
    report_id: int
    three_lines: list[str] = Field(min_length=3, max_length=3)
    sections: list[SectionSummary]
    created_at: datetime = Field(default_factory=datetime.now)
    model: str = "gpt-4o-mini"

# 3줄 요약 - summarize_three_lines()의 결과
class ThreeLineSummary(BaseModel):
    reasoning: str = Field(description="(내부용) 핵심 주장 -> 근거 수치 -> 시사점 순서로 정리한 생각 과정. 화면에 표시하지는 않음")
    lines: list[str] = Field(min_length=3, max_length=3, description="최종 3줄. 1줄: 핵심 주장, 2줄: 근거 수치, 3줄: 시사점. 각 줄 60자 이내")

# 검증 스키마
class VerifyResult(BaseModel):
    """verify_and_expand() 결과: 보고서 수치를 ECOS 최신 통계와 비교한 결과"""
    matches: list[str] = Field(default_factory=list, description="보고서 수치와 ECOS 값이 일치, 근사한 항목." \
    "형식: '지표명(기준 시점): 보고서 값 / ECOS 값 [상태] (출처)'")
    differences: list[str] = Field(default_factory=list, description="보고서 수치와 ECOS 값이 다른 항목. 형식은 matches와 같음")
    unchecked: list[str] = Field(default_factory=list, description="검증하지 못한 항목과 이유. 예: 미래 전망, ECOS 데이터 값 없음, 단위, 기준이 달라 비교 불가")
    additions: list[str] = Field(default_factory=list, description="근거가 부족한 부분을 ECOS 값으로 보완한 문단. 문장마다 출처 표시")
    checked_at: datetime = Field(default_factory=datetime.now)

# 병합 스키마
class MergedReport(BaseModel):
    common: list[str] = Field(description="모든 보고서가 공통으로 말하는 내용. 항목마다 근거 보고서 제목을 괄호로 표시")
    differences: list[str] = Field(description="보고서 간 의견, 수치가 다른 부분. 예: '2026 성장률 전망: A 2.1%, B 1.8%'")
    conclusion: str = Field(description="공통점과 차이를 종합한 결론 3문장 이내")
