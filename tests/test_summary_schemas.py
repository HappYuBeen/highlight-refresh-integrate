"""요약 스키마 테스트 (API 호출 없음, 비용 0원)

실행: 프로젝트 최상위에서
    poetry run pytest tests/test_summary_schemas.py -v
"""
import pytest
from pydantic import ValidationError

from hri.domain.schemas.summary import (
    MergedReport,
    ReportSummary,
    SectionSummary,
    ThreeLineSummary,
    VerifyResult,
)


# ─────────────────────────────────────────────────────────────
# 1. SectionSummary (섹션별 요약)
# ─────────────────────────────────────────────────────────────
def test_section_summary_정상():
    s = SectionSummary(title="Ⅱ. 국내 경제 동향", summary="GDP가 0.6% 성장했다.",
                       section_id=3, start_page=3, end_page=7)
    assert (s.start_page, s.end_page, s.section_id) == (3, 7, 3)


def test_section_summary_한_페이지_섹션():
    s = SectionSummary(title="요약", summary="...", start_page=2, end_page=2)
    assert s.start_page == s.end_page == 2


def test_section_summary_AI결과만으로_생성():
    # AI는 title·summary만 주므로 페이지가 비어 있어도 만들어져야 함
    s = SectionSummary(title="서론", summary="요약")
    assert s.section_id is None and s.start_page is None and s.end_page is None


@pytest.mark.parametrize("pages", [
    {"start_page": 0, "end_page": 1},     # 0페이지 (0부터 세는 실수)
    {"start_page": 1, "end_page": 0},
    {"start_page": 5, "end_page": 3},     # 끝 < 시작
])
def test_section_summary_잘못된_페이지(pages):
    with pytest.raises(ValidationError):
        SectionSummary(title="x", summary="y", **pages)


@pytest.mark.parametrize("data", [
    {"title": "제목만 있음"},
    {"summary": "요약만 있음"},
    {},
])
def test_section_summary_필수값_누락(data):
    with pytest.raises(ValidationError):
        SectionSummary(**data)


# ─────────────────────────────────────────────────────────────
# 2. ThreeLineSummary (3줄 요약)
# ─────────────────────────────────────────────────────────────
def test_three_lines_정확히_3줄():
    t = ThreeLineSummary(reasoning="주장→근거→시사점",
                         lines=["성장 둔화", "1분기 GDP 0.6%", "내수 대책 필요"])
    assert len(t.lines) == 3


@pytest.mark.parametrize("lines", [[], ["1"], ["1", "2"], ["1", "2", "3", "4"]])
def test_three_lines_개수_틀림(lines):
    with pytest.raises(ValidationError):
        ThreeLineSummary(reasoning="...", lines=lines)


def test_three_lines_reasoning_누락():
    with pytest.raises(ValidationError):
        ThreeLineSummary(lines=["a", "b", "c"])


# ─────────────────────────────────────────────────────────────
# 3. ReportSummary (summary_json 캐시)
# ─────────────────────────────────────────────────────────────
def make_report_summary() -> ReportSummary:
    return ReportSummary(
        report_id=12,
        three_lines=["a", "b", "c"],
        sections=[
            SectionSummary(title="서론", summary="요약1", section_id=1, start_page=1, end_page=2),
            SectionSummary(title="본론", summary="요약2", section_id=2, start_page=3, end_page=7),
        ],
    )


def test_report_summary_json_왕복():
    original = make_report_summary()
    text = original.model_dump_json()                      # DB에 저장하는 글자
    restored = ReportSummary.model_validate_json(text)     # DB에서 꺼내 복원
    assert restored == original


def test_report_summary_한글_보존():
    text = make_report_summary().model_dump_json()
    assert "서론" in ReportSummary.model_validate_json(text).sections[0].title


def test_report_summary_기본값():
    r = make_report_summary()
    assert r.created_at is not None and r.model == "gpt-4o-mini"


@pytest.mark.parametrize("broken", [
    '{"report_id": 12, "three_lines": ["a"], "sections": []}',          # 3줄 아님
    '{"report_id": 12, "three_lines": ["a", "b", "c"]}',               # sections 없음
    '{"report_id": "열두", "three_lines": ["a", "b", "c"], "sections": []}',  # 숫자 아님
    '{"report_id": 12, "three_lines": ["a", "b", "c"], "sections": [',  # 깨진 JSON
])
def test_report_summary_깨진_캐시는_에러(broken):
    with pytest.raises(ValidationError):
        ReportSummary.model_validate_json(broken)


def test_report_summary_섹션_페이지_검사도_적용():
    with pytest.raises(ValidationError):
        ReportSummary(report_id=1, three_lines=["a", "b", "c"],
                      sections=[{"title": "x", "summary": "y", "start_page": 5, "end_page": 3}])


# ─────────────────────────────────────────────────────────────
# 4. VerifyResult (검증·보완)
# ─────────────────────────────────────────────────────────────
def test_verify_result_빈_목록_기본값():
    r = VerifyResult()
    assert r.matches == r.differences == r.unchecked == r.additions == []
    assert r.checked_at is not None


def test_verify_result_기본_목록이_공유되지_않음():
    a, b = VerifyResult(), VerifyResult()
    a.matches.append("항목")
    assert b.matches == []          # default_factory=list 확인


def test_verify_result_json_왕복():
    r = VerifyResult(
        matches=["경제성장률(2026Q1): 보고서 0.6% / ECOS 0.6% [일치] (ECOS)"],
        differences=["원/달러 환율(2026-09): 보고서 1,350원 / ECOS 1,388원 [차이] (ECOS)"],
        unchecked=["경제성장률(2027 전망): 미래 전망이라 비교 불가"],
        additions=["9월 평균 환율은 1,388원입니다. (ECOS)"],
    )
    assert VerifyResult.model_validate_json(r.model_dump_json()) == r


# ─────────────────────────────────────────────────────────────
# 5. MergedReport (병합)
# ─────────────────────────────────────────────────────────────
def test_merged_report_정상():
    m = MergedReport(common=["성장 둔화 (A, B)"],
                     differences=["2026 성장률: A 2.1%, B 1.8%"],
                     conclusion="두 보고서 모두 성장 둔화를 전망합니다.")
    assert m.conclusion


@pytest.mark.parametrize("missing", ["common", "differences", "conclusion"])
def test_merged_report_필수값_누락(missing):
    data = {"common": ["c"], "differences": ["d"], "conclusion": "e"}
    data.pop(missing)
    with pytest.raises(ValidationError):
        MergedReport(**data)


# ─────────────────────────────────────────────────────────────
# 6. AI에게 보내는 양식 (with_structured_output이 쓰는 JSON 스키마)
# ─────────────────────────────────────────────────────────────
def ai_fields(model) -> set[str]:
    return set(model.model_json_schema()["properties"])


def test_section_summary_AI에게는_제목과_요약만():
    assert ai_fields(SectionSummary) == {"title", "summary"}      # 페이지는 숨김


def test_three_lines_AI_양식():
    assert ai_fields(ThreeLineSummary) == {"reasoning", "lines"}


def test_merged_report_AI_양식():
    assert ai_fields(MergedReport) == {"common", "differences", "conclusion"}


@pytest.mark.parametrize("model", [SectionSummary, ThreeLineSummary, MergedReport])
def test_AI가_채우는_필드에는_설명이_있음(model):
    for name, prop in model.model_json_schema()["properties"].items():
        assert prop.get("description"), f"{model.__name__}.{name}에 description이 없습니다"


def test_langchain_양식에서도_페이지_숨김():
    pytest.importorskip("langchain_core")       # langchain이 없으면 이 테스트만 건너뜀
    from langchain_core.utils.function_calling import convert_to_openai_tool
    params = convert_to_openai_tool(SectionSummary)["function"]["parameters"]
    assert set(params["properties"]) == {"title", "summary"}
