"""
요약 캐시 테스트 (서동안, WBS 4.1.4)
summarize_report()를 두 번 부를 때 처음에는 요약을 만들어 reports.summary_json에 저장하고,
두 번째에는 저장된 요약을 그대로 돌려주는지(LLM 호출 없음) 확인한다.

DB·LLM 없이 실행한다.
	- DB: report_dao.get_connection을 메모리 가짜 연결로 바꾼다. select_summary_json()·update_summary_json()은
	  진짜 코드를 그대로 써서 JSON 문자열로 저장·읽기가 실제와 같게 돈다.
	- LLM: summarize_sections()·summarize_three_lines()를 호출 횟수를 세는 가짜로 바꾼다.
기존 test_graphs.py(그래프 분기)·test_summary_service.py(사용량 기록)와 겹치지 않게,
'저장 → 읽기' 왕복과 캐시가 깨졌을 때 다시 만드는지를 본다.
"""

import json
import sys
import types

import pytest

from hri.domain.schemas import ReportSummary, Section, SectionSummary
from hri.exception.errors import OutputFormatError


# ---------------------------------------------------------------------
# 가짜 DB (reports.summary_json만 흉내)
# ---------------------------------------------------------------------
class FakeDB:
	"""reports 테이블의 summary_json 칸만 메모리에 둔다. get_connection() 대신 쓴다."""

	def __init__(self):
		self.summary_json: dict[int, object] = {1: None, 2: None}  # report_id → 저장된 값 (MySQL JSON 칸)
		self.updates: list[int] = []

	def connect(self):
		db = self

		class _Cursor:
			rowcount = 0

			def __enter__(self):
				return self

			def __exit__(self, *exc):
				return False

			def execute(self, sql, params):
				self.sql = sql
				if sql.startswith("SELECT summary_json"):
					report_id = params[0]
					self.row = {"summary_json": db.summary_json[report_id]} if report_id in db.summary_json else None
				elif sql.startswith("UPDATE reports SET summary_json"):
					value, report_id = params
					self.rowcount = 1 if report_id in db.summary_json else 0
					if self.rowcount:
						db.summary_json[report_id] = value
						db.updates.append(report_id)

			def fetchone(self):
				return self.row

		class _Conn:
			def __enter__(self):
				return self

			def __exit__(self, *exc):
				return False

			def cursor(self):
				return _Cursor()

			def commit(self):
				pass

		return _Conn()


SECTIONS = {
	1: [
		Section(report_id=1, section_no=0, title="1. 개요", start_page=7, end_page=7, text="1. 개요\n환율 상승 우려가 커졌다."),
		Section(report_id=1, section_no=1, title="3. 영향", start_page=10, end_page=13, text="3. 영향\n환율 10% 상승 시 0.3~0.5%p."),
	],
	2: [Section(report_id=2, section_no=0, title="3. 종합 전망", start_page=13, end_page=13, text="하반기 2.5~3.0%.")],
}


@pytest.fixture
def env(monkeypatch):
	"""summarize_report()를 DB·LLM 없이 돌릴 수 있게 바꿔 끼우고, 호출 기록을 돌려준다."""
	# llm_client는 import할 때 .env가 필요하므로 가짜 모듈로 바꾼다 (요약 함수도 가짜라 LLM은 안 부름)
	llm = types.ModuleType("hri.llm.llm_client")
	llm.get_chat_model = lambda **kw: pytest.fail("LLM을 직접 부르면 안 됩니다")
	llm.cost_korean_won = lambda usage, rate: 0.0
	llm.get_usd_krw = lambda: 1400.0
	monkeypatch.setitem(sys.modules, "hri.llm.llm_client", llm)
	for name in ("hri.prompt.summary_prompt", "hri.service.summary_service"):
		monkeypatch.delitem(sys.modules, name, raising=False)

	import hri.dao.report_dao as report_dao
	import hri.prompt.summary_prompt as summary_prompt
	import hri.service.summary_service as summary_service
	from hri.graph.registry import get_summary_graph

	db = FakeDB()
	calls = {"sections": 0, "three_lines": 0}

	def fake_summarize_sections(sections):
		calls["sections"] += 1
		return [SectionSummary(title=s.title, summary=f"{s.title} 요약입니다. 수치는 원문 그대로.",
			section_id=s.section_no, start_page=s.start_page, end_page=s.end_page) for s in sections]

	def fake_summarize_three_lines(summaries):
		calls["three_lines"] += 1
		from hri.domain.schemas import ThreeLineSummary
		return ThreeLineSummary(reasoning="주장 → 수치 → 시사점",
			lines=["환율 상승은 물가 상방 압력이다.", "10% 상승 시 0.3~0.5%p 오른다.", "외환시장 안정이 필요하다."])

	monkeypatch.setattr(report_dao, "get_connection", db.connect)
	monkeypatch.setattr(report_dao, "select_sections", lambda report_id: SECTIONS.get(report_id, []))
	monkeypatch.setattr(summary_prompt, "summarize_sections", fake_summarize_sections)
	monkeypatch.setattr(summary_prompt, "summarize_three_lines", fake_summarize_three_lines)
	monkeypatch.setattr(summary_service, "insert_usage", lambda usage: None)
	get_summary_graph.cache_clear()
	yield types.SimpleNamespace(db=db, calls=calls, summarize_report=summary_service.summarize_report)
	get_summary_graph.cache_clear()


def llm_calls(env) -> int:
	return env.calls["sections"] + env.calls["three_lines"]


# [확인 1] 처음 호출: 캐시가 없어 섹션마다 요약 + 3줄 요약을 만들고 summary_json에 저장한다
def test_first_call_summarizes_and_saves(env):
	result = env.summarize_report(1)

	assert env.calls == {"sections": 2, "three_lines": 1}   # 섹션 2개는 1개씩 따로, 3줄은 1번
	assert env.db.updates == [1]
	assert result.three_lines[1] == "10% 상승 시 0.3~0.5%p 오른다."


# [확인 2] 저장된 값은 ReportSummary JSON 문자열이고, 한글이 그대로 들어 있다
def test_saved_value_is_report_summary_json(env):
	env.summarize_report(1)
	saved = env.db.summary_json[1]

	assert isinstance(saved, str)
	data = json.loads(saved)
	assert data["report_id"] == 1 and len(data["three_lines"]) == 3
	assert "환율 상승은" in saved                               # \uXXXX로 바뀌지 않음
	assert [s["start_page"] for s in data["sections"]] == [7, 10]


# [확인 3] 두 번째 호출: 저장된 요약을 그대로 돌려주고 LLM을 부르지 않으며 다시 저장하지 않는다
def test_second_call_uses_cache(env):
	first = env.summarize_report(1)
	before = llm_calls(env)
	second = env.summarize_report(1)

	assert llm_calls(env) == before
	assert env.db.updates == [1]
	assert second.three_lines == first.three_lines
	assert [(s.title, s.start_page, s.end_page, s.summary) for s in second.sections] == \
		[(s.title, s.start_page, s.end_page, s.summary) for s in first.sections]


# [확인 4] DB 드라이버가 JSON 칸을 dict로 풀어 줘도 캐시로 읽는다
def test_cache_read_when_driver_returns_dict(env):
	first = env.summarize_report(1)
	env.db.summary_json[1] = json.loads(env.db.summary_json[1])
	before = llm_calls(env)

	assert env.summarize_report(1).three_lines == first.three_lines
	assert llm_calls(env) == before


# [확인 5] 캐시가 깨졌거나(JSON 아님) 스키마가 바뀌어 읽을 수 없으면 새로 요약해 덮어쓴다
@pytest.mark.parametrize("broken", ["{깨진 json", json.dumps({"report_id": 1, "sections": []})],
	ids=["JSON_아님", "three_lines_없음"])
def test_broken_cache_regenerates(env, broken):
	env.db.summary_json[1] = broken
	result = env.summarize_report(1)

	assert env.calls == {"sections": 2, "three_lines": 1}
	assert env.db.updates == [1]
	assert ReportSummary.model_validate_json(env.db.summary_json[1]).three_lines == result.three_lines


# [확인 6] 캐시는 보고서별이다: 1번 보고서 캐시가 2번 보고서 요약에 쓰이지 않는다
def test_cache_is_per_report(env):
	env.summarize_report(1)
	before = env.calls["sections"]
	result = env.summarize_report(2)

	assert env.calls["sections"] == before + 1                 # 2번 보고서 섹션 1개를 새로 요약
	assert result.report_id == 2 and [s.title for s in result.sections] == ["3. 종합 전망"]
	assert env.db.updates == [1, 2]


# [확인 7] 섹션이 없는 보고서는 OutputFormatError, 캐시에 아무것도 저장하지 않는다
def test_no_sections_not_cached(env):
	env.db.summary_json[3] = None
	with pytest.raises(OutputFormatError):
		env.summarize_report(3)
	assert env.db.summary_json[3] is None and env.db.updates == []