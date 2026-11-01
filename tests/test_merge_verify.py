"""병합·검증 테스트 · merge_reports, verify_and_expand (WBS 3.4.7·3.4.8)

가짜 모델(FakeToolChatModel)과 가짜 서비스·API 모듈로 바꿔 끼워 네트워크·DB·API 키·.env 없이 실행한다.
- summary_prompt는 get_chat_model을 모듈 맨 위에서 import하므로 hri.prompt.summary_prompt.get_chat_model을 바꾼다.
- 서비스·API는 함수 안에서 import하므로 sys.modules에 가짜 모듈을 끼운다.

실행: poetry run python -m pytest tests/test_merge_verify.py -v
"""

import sys
import types
from datetime import date, datetime

import pytest

from hri.domain.schemas import EcosQuery, Indicator, MergedReport, Report, ReportSummary, SectionSummary, VerifyResult
from hri.exception.errors import ExternalApiError, OutputFormatError
from hri.prompt import summary_prompt
from hri.prompt.summary_prompt import (
	MAX_MERGE_CHARS,
	MAX_UNCHECKED_NAMES,
	MERGE_MAX_TOKENS,
	NO_ECOS_QUERY_NOTE,
	NO_INDICATORS_NOTE,
	VERIFY_MAX_TOKENS,
	merge_reports,
	verify_and_expand,
)
from tests.fakes import FakeToolChatModel


def fake_module(monkeypatch, name, **attrs):
	"""sys.modules에 가짜 모듈을 끼운다 (테스트가 끝나면 pytest가 되돌린다)."""
	module = types.ModuleType(name)
	for key, value in attrs.items():
		setattr(module, key, value)
	monkeypatch.setitem(sys.modules, name, module)


@pytest.fixture
def install(monkeypatch):
	"""get_chat_model을 가짜로 바꾼다. (가짜 모델, get_chat_model에 넘어온 인자 목록)을 돌려준다."""

	def _install(*responses):
		fake = FakeToolChatModel(responses=list(responses))
		kwargs_log = []

		def factory(**kwargs):
			kwargs_log.append(kwargs)
			return fake

		monkeypatch.setattr(summary_prompt, "get_chat_model", factory)
		return fake, kwargs_log

	return _install


def _text(message) -> str:
	return message[1] if isinstance(message, tuple) else message.content


def _report(report_id, title):
	return Report(report_id=report_id, title=title, file_path=f"data/raw/{report_id}.pdf", uploaded_by="tester",
		published_date=date(2026, 7, 31))


def _summary(report_id, *pairs):
	sections = [SectionSummary(title=title, summary=text) for title, text in pairs]
	return ReportSummary(report_id=report_id, three_lines=["가", "나", "다"], sections=sections)


def _merged(conclusion=" 결론입니다 ", common=("공통(A)",), differences=("차이",)):
	return MergedReport(common=list(common), differences=list(differences), conclusion=conclusion)


def _indicator(name="경제성장률", value=2.1, period="2025", unit="%", source="text", needs_review=False):
	return Indicator(name=name, value=value, unit=unit, period=period, source=source, needs_review=needs_review)


# ─────────────────────────────────────────────────────────────
# merge_reports
# ─────────────────────────────────────────────────────────────
@pytest.fixture
def merge_deps(monkeypatch):
	"""가짜 summarize_report·select_reports. 호출 기록을 돌려준다."""
	calls = []
	summaries = {
		12: _summary(12, ("1. 전망", "성장률 2.1% 전망"), ("2. 리스크", "수출 둔화")),
		7: _summary(7, ("개요", "성장률 1.8% 전망")),
	}

	def summarize_report(report_id):
		calls.append(report_id)
		return summaries[report_id]

	fake_module(monkeypatch, "hri.service.summary_service", summarize_report=summarize_report)
	fake_module(monkeypatch, "hri.dao.report_dao", select_reports=lambda keyword: [_report(12, "A 보고서"), _report(7, "B 보고서")])
	return calls


# [확인 1] 보고서마다 요약을 가져와 '### 제목' 구분자로 한 번에 LLM에 보내고, 결과 결론은 공백을 정리한다
def test_merge_reports(install, merge_deps):
	fake, kwargs_log = install(_merged())
	result = merge_reports([12, 7])

	assert merge_deps == [12, 7]
	assert kwargs_log == [{"temperature": 0, "max_tokens": MERGE_MAX_TOKENS}]
	assert fake.structured_schemas == [MergedReport]
	prompt = _text(fake.calls[0][1])
	assert prompt.index("### A 보고서") < prompt.index("### B 보고서")
	assert "[1. 전망] 성장률 2.1% 전망" in prompt and "[개요] 성장률 1.8% 전망" in prompt
	assert result.conclusion == "결론입니다"


# [확인 2] 같은 번호는 한 번만 쓰고, 보고서가 2개 미만이면 서비스·LLM을 부르지 않고 ValueError
@pytest.mark.parametrize("ids", [[], [12], [12, 12]])
def test_merge_reports_needs_two(install, merge_deps, ids):
	fake, kwargs_log = install()
	with pytest.raises(ValueError):
		merge_reports(ids)
	assert merge_deps == [] and kwargs_log == []


# [확인 3] 중복 번호가 섞여도 요약은 보고서마다 한 번만 가져온다
def test_merge_reports_dedupes(install, merge_deps):
	install(_merged())
	merge_reports([12, 7, 12])
	assert merge_deps == [12, 7]


# [확인 4] DB에서 제목을 못 찾으면 '보고서 번호'로 쓴다
def test_merge_reports_title_fallback(install, monkeypatch, merge_deps):
	fake_module(monkeypatch, "hri.dao.report_dao", select_reports=lambda keyword: [_report(12, "A 보고서")])
	fake, _ = install(_merged())
	merge_reports([12, 7])
	assert "### 보고서 7" in _text(fake.calls[0][1])


# [확인 5] 보고서 1건의 요약은 MAX_MERGE_CHARS자까지만 보낸다
def test_merge_reports_truncates(install, monkeypatch, merge_deps):
	long = _summary(12, ("긴 섹션", "가" * (MAX_MERGE_CHARS * 2)))
	fake_module(monkeypatch, "hri.service.summary_service", summarize_report=lambda report_id: long if report_id == 12 else _summary(7, ("개요", "짧음")))
	fake, _ = install(_merged())
	merge_reports([12, 7])
	body = _text(fake.calls[0][1]).split("### B 보고서")[0].split("### A 보고서\n")[1]
	assert len(body.strip()) <= MAX_MERGE_CHARS


# [확인 6] 형식이 어긋나면(결론이 빔·공통과 차이가 모두 빔) 한 번 더 시도하고, 계속 어긋나면 OutputFormatError
def test_merge_reports_retry_and_fail(install, merge_deps):
	fake, _ = install(_merged(conclusion="  "), _merged())
	assert merge_reports([12, 7]).conclusion == "결론입니다"
	assert len(fake.calls) == 2

	fake, _ = install(_merged(conclusion="  "), _merged(common=(), differences=()))
	with pytest.raises(OutputFormatError):
		merge_reports([12, 7])


# [확인 7] 요약 단계의 OutputFormatError는 그대로 올라간다
def test_merge_reports_summary_error(install, monkeypatch, merge_deps):
	def broken(report_id):
		raise OutputFormatError("요약 실패")

	fake_module(monkeypatch, "hri.service.summary_service", summarize_report=broken)
	fake, kwargs_log = install()
	with pytest.raises(OutputFormatError):
		merge_reports([12, 7])
	assert kwargs_log == []


# ─────────────────────────────────────────────────────────────
# verify_and_expand
# ─────────────────────────────────────────────────────────────
QUERY = EcosQuery(stat_code="200Y102", cycle="A", item_code="10111", start="2025", end="2025", indicator_name="경제성장률")


@pytest.fixture
def verify_deps(monkeypatch):
	"""가짜 extract_indicators·find_usable_data·fetch_indicators. 각 단계를 바꿀 수 있는 dict를 돌려준다."""
	state = {
		"indicators": [_indicator(), _indicator(value=3.0, period="2026"), _indicator(name="소비자물가", value=2.0)],
		"query": QUERY,
		"ecos": [_indicator(value=1.9, source="table")],
		"calls": [],
	}

	def extract(report_id):
		state["calls"].append(("extract", report_id))
		return state["indicators"]

	def find(indicators):
		state["calls"].append(("find", [i.name for i in indicators]))
		return state["query"]

	def fetch(query, name):
		state["calls"].append(("fetch", query.stat_code, name))
		if isinstance(state["ecos"], Exception):
			raise state["ecos"]
		return state["ecos"]

	fake_module(monkeypatch, "hri.service.indicator_service", extract_indicators=extract)
	fake_module(monkeypatch, "hri.api.ecos_finder", find_usable_data=find)
	fake_module(monkeypatch, "hri.api.ecos_client", fetch_indicators=fetch)
	return state


# [확인 8] 지표 → 조회 조건 → ECOS 값 순서로 부르고, 요청·보고서 값·ECOS 값을 LLM에 보낸다
def test_verify_and_expand(install, verify_deps):
	old = datetime(2000, 1, 1)
	fake, kwargs_log = install(VerifyResult(matches=["경제성장률(2025): 2.1 / 1.9 [근사] (출처: 한국은행 ECOS 200Y102)"], checked_at=old))
	result = verify_and_expand(12, " 성장률 맞는지 확인 ")

	assert [c[0] for c in verify_deps["calls"]] == ["extract", "find", "fetch"]
	assert verify_deps["calls"][2] == ("fetch", "200Y102", "경제성장률")
	assert kwargs_log == [{"temperature": 0, "max_tokens": VERIFY_MAX_TOKENS}]
	assert fake.structured_schemas == [VerifyResult]
	prompt = _text(fake.calls[0][1])
	assert "[사용자 요청]\n성장률 맞는지 확인" in prompt
	assert "경제성장률 (2025): 2.1 %" in prompt and "경제성장률 (2026): 3.0 %" in prompt
	assert "소비자물가" not in prompt.split("[ECOS 값]")[0].split("[보고서 수치]")[1]  # 비교하지 않는 지표는 LLM에 보내지 않는다
	assert "[ECOS 200Y102]" in prompt
	assert result.matches and result.checked_at > old  # checked_at은 코드가 현재 시각으로 채운다


# [확인 9] 비교하지 않은 지표 이름은 unchecked에 적는다 (이름이 많으면 MAX_UNCHECKED_NAMES개까지)
def test_verify_and_expand_skipped_note(install, verify_deps):
	install(VerifyResult(matches=["ok"], unchecked=["LLM이 쓴 이유"]))
	result = verify_and_expand(12, "확인")
	assert result.unchecked[0] == "LLM이 쓴 이유"
	assert result.unchecked[1].startswith("소비자물가:") and "비교하지 않았습니다" in result.unchecked[1]

	verify_deps["indicators"] = [_indicator()] + [_indicator(name=f"지표{n:02d}") for n in range(15)]
	install(VerifyResult(matches=["ok"]))
	note = verify_and_expand(12, "확인").unchecked[0]
	names = note.split(":")[0].split(", ")
	assert len(names) == MAX_UNCHECKED_NAMES and names == sorted(names)
	assert "지표09" in names and "지표10" not in names

	verify_deps["indicators"] = [_indicator()]
	install(VerifyResult(matches=["ok"]))
	assert verify_and_expand(12, "확인").unchecked == []


# [확인 10] 지표가 없거나 비교할 통계·값이 없으면 LLM을 부르지 않고 unchecked에 이유만 담는다
def test_verify_and_expand_without_llm(install, verify_deps):
	fake, kwargs_log = install()

	verify_deps["indicators"] = []
	assert verify_and_expand(12, "확인").unchecked == [NO_INDICATORS_NOTE]
	assert [c[0] for c in verify_deps["calls"]] == ["extract"]

	verify_deps["indicators"] = [_indicator()]
	verify_deps["query"] = None
	assert verify_and_expand(12, "확인").unchecked == [NO_ECOS_QUERY_NOTE]

	verify_deps["query"] = QUERY
	verify_deps["ecos"] = []
	assert "경제성장률" in verify_and_expand(12, "확인").unchecked[0]
	assert kwargs_log == []


# [확인 11] 형식이 어긋나면(모든 항목이 빔) 한 번 더 시도하고, 계속 어긋나면 OutputFormatError
def test_verify_and_expand_retry_and_fail(install, verify_deps):
	fake, _ = install(VerifyResult(), VerifyResult(matches=["ok"]))
	assert verify_and_expand(12, "확인").matches == ["ok"]
	assert len(fake.calls) == 2

	install(VerifyResult(), VerifyResult())
	with pytest.raises(OutputFormatError):
		verify_and_expand(12, "확인")


# [확인 12] ECOS 호출 실패(ExternalApiError)는 그대로 올라간다 (도구·화면이 안내 문구로 바꾼다)
def test_verify_and_expand_external_error(install, verify_deps):
	verify_deps["ecos"] = ExternalApiError("ECOS API 호출 실패")
	fake, kwargs_log = install()
	with pytest.raises(ExternalApiError):
		verify_and_expand(12, "확인")
	assert kwargs_log == []


# [확인 13] 차트에서 온 추정값(needs_review)은 '추정'으로 표시해서 보낸다
def test_verify_and_expand_marks_estimates(install, verify_deps):
	verify_deps["indicators"] = [_indicator(source="chart", needs_review=True)]
	fake, _ = install(VerifyResult(matches=["ok"]))
	verify_and_expand(12, "확인")
	assert "[chart, 추정]" in _text(fake.calls[0][1])
