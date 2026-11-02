"""4.1.4 요약 품질·JSON 형식 점검 — 3줄·섹션·병합 화면과 재요청 동작 (서동안)

확인하는 것 (기능 명세서 v6: on_summarize·on_merge·summarize_sections·summarize_three_lines·merge_reports·OutputFormatError)
	1. JSON 형식: 3줄 요약은 정확히 3줄만 받고, 요약 결과(ReportSummary)는 JSON으로 저장했다가 그대로 다시 읽힌다
	2. 재요청 동작: 형식이 어긋나면 한 번 더 요청하고(MAX_ATTEMPTS=2), 두 번 모두 실패하면 OutputFormatError
	3. 요약 화면(on_summarize): 3줄/섹션 보기 전환, 3줄은 1·2·3 번호, 섹션은 제목·쪽 범위, 보고서 1건 = 1페이지(이전·다음), 한 보고서가 실패해도 나머지는 표시
	4. 병합 화면(on_merge): 2~5개만 허용, 진행 상황 표시, 공통 의견·차이점·종합 결론, 요약 실패 시 병합하지 않음
	5. 병합 입력: 섹션 요약이 길어도 모든 섹션이 조금씩 들어간다 (뒤쪽 섹션이 통째로 빠지지 않음)

LLM·DB 없이 가짜 모델과 가짜 함수로 실행한다 (.env는 모듈을 불러올 때만 필요).
실행: poetry run python -m pytest tests/test_summary_format.py -v
"""

import importlib

import pydantic
import pytest

from hri.domain.schemas import MergedReport, ReportSummary, SectionSummary, ThreeLineSummary
from hri.exception.errors import OutputFormatError
from hri.prompt import summary_prompt
from hri.view import summary_view

THREE = ["물가 상승세가 하반기에 커질 전망이다.", "하반기 상승률은 2.5~3.0%로 예상된다.", "생활물가 관리가 필요하다."]


def _section(no: int, title: str, start: int, end: int, text: str = "요약 문장입니다.") -> SectionSummary:
	return SectionSummary(title=title, summary=text, section_id=no, start_page=start, end_page=end)


def _summary(report_id: int = 1) -> ReportSummary:
	return ReportSummary(report_id=report_id, three_lines=THREE,
		sections=[_section(0, "1. 개요", 3, 4), _section(1, "2. 품목별 동향", 5, 8)])


class FakeModel:
	"""정해 둔 응답을 차례로 돌려주는 가짜 LLM. Exception을 넣으면 그 차례에 예외를 낸다."""

	def __init__(self, *responses):
		self.responses = list(responses)
		self.calls = 0

	def with_structured_output(self, schema):
		return self

	def invoke(self, messages):
		self.calls += 1
		item = self.responses.pop(0)
		if isinstance(item, Exception):
			raise item
		return item


@pytest.fixture
def use_model(monkeypatch):
	def install(*responses):
		model = FakeModel(*responses)
		monkeypatch.setattr(summary_prompt, "get_chat_model", lambda **kwargs: model)
		return model
	return install


# =====================================================================
# 1. JSON 형식
# =====================================================================

# [확인 1] 3줄 요약은 정확히 3줄이어야 한다 (2줄·4줄은 스키마가 거부)
@pytest.mark.parametrize("lines", [THREE[:2], THREE + ["넷째 줄"]], ids=["2줄", "4줄"])
def test_three_lines_schema_needs_exactly_three(lines):
	with pytest.raises(pydantic.ValidationError):
		ThreeLineSummary(reasoning="r", lines=lines)
	with pytest.raises(pydantic.ValidationError):
		ReportSummary(report_id=1, three_lines=lines, sections=[])


# [확인 2] 요약 결과는 JSON으로 저장했다가 그대로 다시 읽힌다 (summary_json 캐시 형식, 한글·물결표 유지)
def test_report_summary_json_round_trip():
	summary = _summary()
	restored = ReportSummary.model_validate_json(summary.model_dump_json())
	assert restored == summary
	assert "2.5~3.0%" in summary.model_dump_json()  # 한글·특수문자가 깨지지 않음


# [확인 3] 섹션 쪽 범위는 끝 쪽이 시작 쪽보다 앞설 수 없다
def test_section_page_range_checked():
	with pytest.raises(pydantic.ValidationError):
		_section(0, "1. 개요", 5, 3)


# =====================================================================
# 2. 재요청 동작 (MAX_ATTEMPTS)
# =====================================================================

# [확인 4] 섹션 요약이 비어 오면 한 번 더 요청하고, 두 번째 응답을 쓴다
def test_section_summary_retries_once(use_model):
	from hri.domain.schemas import Section

	model = use_model(SectionSummary(title="x", summary="  "), SectionSummary(title="x", summary="두 번째 요약입니다."))
	section = Section(section_no=0, title="1. 개요", start_page=3, end_page=4, text="본문")
	result = summary_prompt.summarize_sections([section])
	assert model.calls == summary_prompt.MAX_ATTEMPTS == 2
	assert result[0].summary == "두 번째 요약입니다." and result[0].title == "1. 개요"


# [확인 5] 호출 오류도 재요청 대상이고, 두 번 모두 실패하면 OutputFormatError
def test_section_summary_fails_after_two_attempts(use_model):
	from hri.domain.schemas import Section

	model = use_model(RuntimeError("타임아웃"), SectionSummary(title="x", summary=""))
	section = Section(section_no=0, title="1. 개요", start_page=3, end_page=4, text="본문")
	with pytest.raises(OutputFormatError):
		summary_prompt.summarize_sections([section])
	assert model.calls == 2


# [확인 6] 3줄 중 빈 줄이 있으면 한 번 더 요청하고, 앞뒤 공백은 뗀다
def test_three_lines_retry_and_strip(use_model):
	model = use_model(ThreeLineSummary(reasoning="r", lines=["a", " ", "c"]),
		ThreeLineSummary(reasoning="r", lines=[f" {line} " for line in THREE]))
	result = summary_prompt.summarize_three_lines([_section(0, "1. 개요", 3, 4)])
	assert model.calls == 2
	assert result.lines == THREE


# [확인 7] 병합 결과에 결론이 없거나 공통·차이가 모두 비면 다시 요청하고, 계속 그러면 OutputFormatError
def test_merge_retry_then_fail(use_model, monkeypatch):
	# merge_reports가 함수 안에서 import하는 모듈(sys.modules에 등록된 것)에 바꿔 끼운다
	# (다른 테스트가 모듈을 다시 불러와도 실제로 쓰이는 쪽에 적용되게)
	summary_service = importlib.import_module("hri.service.summary_service")
	monkeypatch.setattr(summary_service, "summarize_report", lambda report_id: _summary(report_id))
	monkeypatch.setattr(summary_prompt, "_report_titles", lambda ids: {i: f"보고서{i}" for i in ids})
	empty = MergedReport(common=[], differences=[], conclusion="결론")
	no_conclusion = MergedReport(common=["공통"], differences=[], conclusion=" ")
	model = use_model(empty, no_conclusion)
	with pytest.raises(OutputFormatError):
		summary_prompt.merge_reports([1, 2])
	assert model.calls == 2


# =====================================================================
# 3. 요약 화면 (on_summarize)
# =====================================================================
@pytest.fixture
def screen(monkeypatch):
	monkeypatch.setattr(summary_view, "_report_titles", lambda: {1: "물가 보고서", 2: "성장 보고서", 3: "환율 보고서"})

	def use(results: dict):
		def fake(report_id):
			value = results[report_id]
			if isinstance(value, Exception):
				raise value
			return value
		monkeypatch.setattr(summary_view, "_summarize_report", fake)
	return use


# [확인 8] 보고서를 고르지 않으면 안내만 보여 준다 (페이지 넘김 줄은 숨김)
def test_summarize_needs_report():
	result = summary_view.on_summarize([])
	assert result[0] == summary_view.NEED_REPORT_MESSAGE
	assert result[3]["visible"] is False


# [확인 9] 3줄 요약 보기: 1·2·3 번호로, 물결표(~)는 취소선이 되지 않게 \~로 보여 준다
def test_summarize_three_lines_markdown(screen):
	screen({1: _summary(1)})
	text = summary_view.on_summarize([1], summary_view.VIEW_THREE_LINES)[0]
	assert "### 물가 보고서" in text
	assert "1. 물가 상승세가" in text and "2. 하반기 상승률은 2.5\\~3.0%" in text and "3. 생활물가" in text


# [확인 10] 섹션별 요약 보기: 섹션 제목과 쪽 범위(p.시작-끝)를 함께 보여 준다
def test_summarize_sections_markdown(screen):
	screen({1: _summary(1)})
	text = summary_view.on_summarize([1], summary_view.VIEW_SECTIONS)[0]
	assert "**1. 개요**p.3-4" in text and "**2. 품목별 동향**p.5-8" in text


# [확인 10-2] 보기 전환: 같은 보고서라도 3줄 요약 보기에는 섹션이, 섹션별 보기에는 3줄이 나오지 않는다
#   (없는 보기 이름은 3줄 요약으로 처리)
def test_view_toggle_shows_only_chosen_view(screen):
	screen({1: _summary(1)})
	three = summary_view.on_summarize([1], summary_view.VIEW_THREE_LINES)[0]
	sections = summary_view.on_summarize([1], summary_view.VIEW_SECTIONS)[0]
	assert "3줄 요약" in three and "섹션별 요약" not in three
	assert "섹션별 요약" in sections and "3줄 요약" not in sections
	assert summary_view.on_summarize([1], "없는 보기")[0] == three
	assert summary_view.on_summarize_three_lines([1])[0] == three and summary_view.on_summarize_sections([1])[0] == sections


# [확인 11] 한 보고서가 실패해도 그 페이지에만 안내를 넣고 나머지는 보여 준다 (형식 오류 / 그 밖의 오류 문구 구분)
def test_summarize_one_failure_keeps_others(screen):
	screen({1: OutputFormatError("형식"), 2: _summary(2), 3: RuntimeError("DB 끊김")})
	pages = summary_view.on_summarize([1, 2, 3])[1]
	assert len(pages) == 3
	assert summary_view.SUMMARY_FAIL_MESSAGE in pages[0]
	assert "1. 물가 상승세가" in pages[1]
	assert summary_view.SUMMARY_ERROR_MESSAGE in pages[2]


# [확인 11-2] 보고서 1건이 1페이지: 1건이면 페이지 넘김 줄이 숨고, 2건 이상이면 보이며 '1 / n'으로 표시된다
def test_one_page_per_report_and_pager_visibility(screen):
	screen({1: _summary(1), 2: _summary(2), 3: _summary(2)})
	one = summary_view.on_summarize([1])
	assert len(one[1]) == 1 and one[3]["visible"] is False
	three = summary_view.on_summarize([1, 2, 3])
	assert len(three[1]) == 3 and three[3]["visible"] is True and three[4] == "**1 / 3**"
	assert three[5]["interactive"] is False and three[6]["interactive"] is True   # 첫 페이지: 이전 비활성


# [확인 11-3] 이전·다음 버튼: 한 장씩 넘기고, 처음·끝을 넘어가지 않으며 버튼 활성 상태가 바뀐다
def test_prev_next_paging(screen):
	pages = ["A", "B", "C"]
	assert summary_view.on_next_page(pages, 0)[0] == "B"
	last = summary_view.on_next_page(pages, 2)
	assert last[0] == "C" and last[2] == 2 and last[6]["interactive"] is False      # 끝에서 다음 → 그대로
	first = summary_view.on_prev_page(pages, 0)
	assert first[0] == "A" and first[2] == 0 and first[5]["interactive"] is False   # 처음에서 이전 → 그대로
	assert summary_view.on_prev_page(pages, 2)[4] == "**2 / 3**"
	assert summary_view.on_clear_pages()[1] == [] and summary_view.on_clear_pages()[3]["visible"] is False


# [확인 11-4] 보고서 선택은 최대 5개: 넘으면 앞에서부터 5개만 남기고 안내한다
def test_select_limit(monkeypatch):
	infos = []
	monkeypatch.setattr(summary_view.gr, "Info", lambda message: infos.append(message))
	assert summary_view.on_limit_reports([1, 2, 3, 4, 5]) == summary_view.gr.update()
	over = summary_view.on_limit_reports([1, 2, 3, 4, 5, 6])
	assert over["value"] == [1, 2, 3, 4, 5] and infos == [summary_view.SELECT_LIMIT_MESSAGE.format(max=5)]


# =====================================================================
# 4. 병합 화면 (on_merge)
# =====================================================================
MERGED = MergedReport(common=["두 보고서 모두 물가 상승 압력을 지적 (물가 보고서, 성장 보고서)"],
	differences=["2026 성장률 전망: 물가 보고서 언급 없음, 성장 보고서 1.7%"], conclusion="물가 관리가 우선입니다.")


@pytest.fixture
def merge_screen(monkeypatch, screen):
	calls = {"merge": 0}

	def fake_merge(ids):
		calls["merge"] += 1
		value = calls.get("result", MERGED)
		if isinstance(value, Exception):
			raise value
		return value

	monkeypatch.setattr(summary_view, "_merge_reports", fake_merge)
	return calls


# [확인 12] 2개 미만·6개 이상이면 병합하지 않고 안내만 보여 준다
@pytest.mark.parametrize("ids,message", [([], "NEED"), ([1], "NEED"), ([1, 2, 3, 4, 5, 6], "TOO_MANY")])
def test_merge_report_count_limits(merge_screen, ids, message):
	out = list(summary_view.on_merge(ids))
	expected = summary_view.NEED_MERGE_MESSAGE if message == "NEED" else summary_view.MERGE_TOO_MANY_MESSAGE.format(max=summary_view.MAX_MERGE_REPORTS)
	assert out == [expected]
	assert merge_screen["merge"] == 0


# [확인 13] 요약 준비 진행 상황(n/전체)을 보여 준 뒤 공통 의견·차이점·종합 결론을 표시한다
def test_merge_shows_progress_and_result(screen, merge_screen):
	screen({1: _summary(1), 2: _summary(2)})
	out = list(summary_view.on_merge([1, 2]))
	assert out[0].startswith("⏳ 요약 준비 중 (1/2)") and out[1].startswith("⏳ 요약 준비 중 (2/2)")
	assert out[2].startswith("⏳ 병합 중")
	final = out[-1]
	for heading in ("**공통 의견**", "**차이점**", "**종합 결론**"):
		assert heading in final
	assert "물가 보고서, 성장 보고서" in final and merge_screen["merge"] == 1


# [확인 14] 한 보고서라도 요약에 실패하면 그 보고서 이름과 사유를 보여 주고 병합 LLM은 부르지 않는다
def test_merge_stops_when_summary_fails(screen, merge_screen):
	screen({1: _summary(1), 2: OutputFormatError("형식")})
	final = list(summary_view.on_merge([1, 2]))[-1]
	assert "병합하지 못했습니다" in final and "성장 보고서" in final and summary_view.SUMMARY_FAIL_MESSAGE in final
	assert merge_screen["merge"] == 0


# [확인 15] 병합 형식 오류·그 밖의 오류는 각각의 안내 문구를 보여 준다
@pytest.mark.parametrize("error,message", [(OutputFormatError("형식"), "MERGE_FAIL_MESSAGE"), (RuntimeError("x"), "MERGE_ERROR_MESSAGE")])
def test_merge_failure_messages(screen, merge_screen, error, message):
	screen({1: _summary(1), 2: _summary(2)})
	merge_screen["result"] = error
	assert list(summary_view.on_merge([1, 2]))[-1] == getattr(summary_view, message)


# [확인 16] 제목이 같은 보고서를 함께 병합하면 중복 업로드 경고를 붙인다
def test_merge_duplicate_title_note(monkeypatch, merge_screen):
	monkeypatch.setattr(summary_view, "_report_titles", lambda: {1: "같은 제목", 2: "같은 제목"})
	monkeypatch.setattr(summary_view, "_summarize_report", lambda report_id: _summary(report_id))
	assert summary_view.MERGE_DUPLICATE_NOTE in list(summary_view.on_merge([1, 2]))[-1]


# =====================================================================
# 5. 병합 입력 길이
# =====================================================================

# [확인 17] 섹션 요약이 길어도 모든 섹션이 병합 입력에 들어가고, 전체는 MAX_MERGE_CHARS자 이내다
def test_merge_body_keeps_every_section():
	sections = [_section(i, f"{i + 1}. 섹션{i + 1}", i + 1, i + 1, "가" * 900) for i in range(12)]
	body = summary_prompt._merge_body(ReportSummary(report_id=1, three_lines=THREE, sections=sections))
	assert len(body) <= summary_prompt.MAX_MERGE_CHARS
	for i in range(12):
		assert f"[{i + 1}. 섹션{i + 1}]" in body