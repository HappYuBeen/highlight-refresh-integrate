"""summarize_report() 단독 테스트 (실제 DB·OpenAI 없이 실행, 비용 0원)

- 진짜 요약 그래프 대신 정해진 state를 돌려주는 가짜 그래프를 끼워 넣는다.
- 가짜 그래프 안에서 가짜 채팅 모델을 호출해 토큰 사용량(UsageMetadataCallbackHandler)도 흉내 낸다.
- get_usd_krw()·cost_korean_won()·insert_usage()는 호출 기록만 남기는 가짜로 바꾼다.

실행: poetry run pytest tests/test_summary_service.py -v
"""
import importlib
import sys
import types

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from hri.domain.schemas import ReportSummary, SectionSummary
from hri.exception.errors import OutputFormatError

MODULE = "hri.service.summary_service"


# ─────────────────────────────────────────────────────────────
# 가짜 부품
# ─────────────────────────────────────────────────────────────
def fake_llm_call(config: dict, input_tokens: int = 100, output_tokens: int = 20) -> None:
    """그래프 안에서 AI를 한 번 부른 것처럼 사용량을 남긴다."""
    msg = AIMessage(content="요약", response_metadata={"model_name": "gpt-4o-mini"},
                    usage_metadata={"input_tokens": input_tokens, "output_tokens": output_tokens,
                                    "total_tokens": input_tokens + output_tokens})
    GenericFakeChatModel(messages=iter([msg])).invoke("x", config=config)


class FakeGraph:
    """graph.invoke(inputs, config)를 흉내 낸다.

    state: invoke가 돌려줄 최종 state
    llm_calls: invoke 안에서 가짜 AI를 몇 번 부를지 (사용량 발생)
    raises: 값이 있으면 AI 호출 뒤에 이 예외를 던짐
    """

    def __init__(self, state, llm_calls=0, raises=None):
        self.state, self.llm_calls, self.raises = state, llm_calls, raises
        self.calls = []

    def invoke(self, inputs, config=None):
        self.calls.append({"inputs": inputs, "config": config})
        for _ in range(self.llm_calls):
            fake_llm_call(config or {})
        if self.raises:
            raise self.raises
        return self.state


def section(title, start, end) -> SectionSummary:
    return SectionSummary(title=title, summary=f"{title} 요약", start_page=start, end_page=end)


LINES = ["핵심 주장", "근거 수치 2.1%", "시사점"]

GENERATED = {  # 새로 요약한 경우의 최종 state (섹션 요약은 끝나는 순서대로 모여 start_page가 뒤섞여 있다)
    "three_lines": LINES,
    "section_summaries": [section("Ⅲ. 시사점", 7, 8), section("Ⅰ. 동향", 1, 3), section("Ⅱ. 물가", 4, 6)],
}


@pytest.fixture
def env(monkeypatch):
    """summary_service가 import하는 3개 모듈을 가짜로 바꾸고, 새로 import한 모듈을 돌려준다."""
    state = {"graph": None, "usage_calls": [], "insert_fails": False, "cost_fails": False}

    registry = types.ModuleType("hri.graph.registry")
    registry.get_summary_graph = lambda: state["graph"]

    def fake_cost_korean_won(usage, usd_to_krw):
        if state["cost_fails"]:
            raise TypeError("단가가 null입니다")      # .env MODEL_PRICES가 null일 때와 같은 오류
        return 1.23

    llm_client = types.ModuleType("hri.llm.llm_client")
    llm_client.get_usd_krw = lambda: 1400.0
    llm_client.cost_korean_won = fake_cost_korean_won

    def fake_insert_usage(usage):
        if state["insert_fails"]:
            raise RuntimeError("DB 연결 실패")
        state["usage_calls"].append(usage)

    usage_dao = types.ModuleType("hri.dao.usage_dao")
    usage_dao.insert_usage = fake_insert_usage

    for name, mod in [("hri.graph.registry", registry), ("hri.llm.llm_client", llm_client),
                      ("hri.dao.usage_dao", usage_dao)]:
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.delitem(sys.modules, MODULE, raising=False)

    state["module"] = importlib.import_module(MODULE)
    return state


def run(env, graph, report_id=5):
    env["graph"] = graph
    return env["module"].summarize_report(report_id)


# ─────────────────────────────────────────────────────────────
# 1. 정상 요약
# ─────────────────────────────────────────────────────────────
def test_그래프에_보고서_번호와_사용량_콜백을_넘김(env):
    graph = FakeGraph(GENERATED)
    run(env, graph, report_id=5)
    call = graph.calls[0]
    assert call["inputs"] == {"report_id": 5}
    assert call["config"]["callbacks"], "사용량 콜백이 config에 들어가야 합니다"


def test_ReportSummary를_돌려줌(env):
    result = run(env, FakeGraph(GENERATED), report_id=5)
    assert isinstance(result, ReportSummary)
    assert result.report_id == 5
    assert result.three_lines == LINES


def test_섹션_요약은_start_page_순으로_정렬(env):
    result = run(env, FakeGraph(GENERATED))
    assert [s.start_page for s in result.sections] == [1, 4, 7]
    assert [s.title for s in result.sections] == ["Ⅰ. 동향", "Ⅱ. 물가", "Ⅲ. 시사점"]


# ─────────────────────────────────────────────────────────────
# 2. 사용량 기록
# ─────────────────────────────────────────────────────────────
def test_AI를_썼으면_모델별로_사용량을_기록(env):
    run(env, FakeGraph(GENERATED, llm_calls=3), report_id=5)
    assert len(env["usage_calls"]) == 1                     # 모델이 gpt-4o-mini 하나뿐이라 한 줄
    usage = env["usage_calls"][0]
    assert usage.flow == "summary" and usage.ref_id == 5
    assert usage.model == "gpt-4o-mini"
    assert usage.input_tokens == 300 and usage.output_tokens == 60    # 3번 호출 합계
    assert usage.cost_krw == 1.23


def test_캐시만_읽었으면_사용량을_기록하지_않음(env):
    result = run(env, FakeGraph(GENERATED, llm_calls=0))
    assert result.three_lines == LINES
    assert env["usage_calls"] == []


def test_비용_계산이_실패해도_토큰은_기록(env):
    env["cost_fails"] = True
    result = run(env, FakeGraph(GENERATED, llm_calls=2), report_id=5)
    assert result.three_lines == LINES
    assert len(env["usage_calls"]) == 1
    usage = env["usage_calls"][0]
    assert usage.input_tokens == 200 and usage.output_tokens == 40
    assert usage.cost_krw is None


def test_사용량_기록이_실패해도_요약은_정상(env):
    env["insert_fails"] = True
    result = run(env, FakeGraph(GENERATED, llm_calls=1))    # 예외가 밖으로 나오면 실패
    assert result.three_lines == LINES


# ─────────────────────────────────────────────────────────────
# 3. 실패 상황
# ─────────────────────────────────────────────────────────────
def test_섹션이_없으면_OutputFormatError(env):
    graph = FakeGraph({"error": "요약할 섹션이 없습니다", "sections": []})
    with pytest.raises(OutputFormatError, match="요약할 섹션이 없습니다"):
        run(env, graph)


def test_그래프가_실패해도_예외를_그대로_전달하고_쓴_비용은_기록(env):
    graph = FakeGraph(GENERATED, llm_calls=2, raises=OutputFormatError("3줄 요약 형식이 올바르지 않습니다."))
    with pytest.raises(OutputFormatError):
        run(env, graph, report_id=9)
    assert len(env["usage_calls"]) == 1, "AI를 썼는데 비용이 기록되지 않았습니다"
    assert env["usage_calls"][0].ref_id == 9