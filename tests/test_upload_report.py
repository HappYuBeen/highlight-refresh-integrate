"""upload_report() 단독 테스트 (실제 PDF·DB·Chroma·OpenAI 없이 실행, 비용 0원)

- 진짜 업로드 그래프 대신 '정해진 순서로 노드 결과를 내보내는 가짜 그래프'를 끼워 넣는다.
- 가짜 그래프 안에서 가짜 채팅 모델을 호출해 토큰 사용량(UsageMetadataCallbackHandler)도 흉내 낸다.
- insert_usage(LlmUsage), cost_korean_won(usage, usd_to_krw), get_usd_krw()는 호출 기록만 남기는 가짜로 바꾼다.

실행: poetry run pytest tests/test_upload_report.py -v
"""
import importlib
import sys
import types

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

MODULE = "hri.service.document_service"


# ─────────────────────────────────────────────────────────────
# 가짜 부품
# ─────────────────────────────────────────────────────────────
def fake_llm_call(config: dict, input_tokens: int = 100, output_tokens: int = 20) -> None:
    """그래프 노드 안에서 AI를 한 번 부른 것처럼 사용량을 남긴다."""
    msg = AIMessage(content="요약", response_metadata={"model_name": "gpt-4o-mini"},
                    usage_metadata={"input_tokens": input_tokens, "output_tokens": output_tokens,
                                    "total_tokens": input_tokens + output_tokens})
    GenericFakeChatModel(messages=iter([msg])).invoke("x", config=config)


class FakeGraph:
    """graph.stream(..., stream_mode="updates")를 흉내 낸다.

    steps: [("노드이름", {바뀐 상태}), ...] 순서대로 내보냄
    llm_at: 이 노드를 내보내기 전에 가짜 AI 호출 (사용량 발생)
    raise_at: 이 노드에서 예외 발생
    """

    def __init__(self, steps, llm_at=(), raise_at=None):
        self.steps, self.llm_at, self.raise_at = steps, set(llm_at), raise_at
        self.calls = []

    def stream(self, inputs, stream_mode=None, config=None):
        self.calls.append({"inputs": inputs, "stream_mode": stream_mode, "config": config})
        for node, state in self.steps:
            if node in self.llm_at:
                fake_llm_call(config or {})
            if node == self.raise_at:
                raise RuntimeError(f"{node} 노드 실패")
            yield {node: state}


META = {"title": "2026 경제전망", "publisher": "현대경제연구원", "published_date": "2026-07-31"}

# warnings는 실제 그래프처럼 persist가 아니라 추출 노드들이 각자 쌓는다 (operator.add 리듀서).
# persist는 {'report_id', 'counts'}만 돌려준다.
SUCCESS_STEPS = [
    ("validate", {"error": None}),
    ("load_text", {"meta": META, "pages": ["p1", "p2", "p3"], "warnings": ["p.2 스캔본 의심"]}),
    ("extract_tables", {}),
    ("process_charts", {"warnings": ["p.14 차트 수치 검토 필요"]}),
    ("split", {}),
    ("summarize_tables", {}),
    ("persist", {"report_id": 12, "counts": {"text": 157, "table": 8, "chart": 3}}),
]

# 실제 validate_node는 실패하면 {'error': reason}을 돌려준다 (통과하면 {'error': None}).
VALIDATE_FAIL_STEPS = [("validate", {"error": "PDF가 아닙니다"})]


@pytest.fixture
def env(monkeypatch):
    """document_service가 import하는 3개 모듈을 가짜로 바꾸고, 새로 import한 모듈을 돌려준다.

    - hri.graph.registry : 가짜 그래프를 돌려줌
    - hri.llm.llm_client : cost_korean_won(usage, usd_to_krw)·get_usd_krw() 가짜 (환율 API 호출 없음)
    - hri.dao.usage_dao  : insert_usage(usage: LlmUsage) 가짜. 받은 LlmUsage를 usage_calls에 쌓는다.
      state['insert_fails']가 True면 DB 오류를 흉내 내 예외를 낸다.
    """
    state = {"graph": None, "usage_calls": [], "insert_fails": False}

    registry = types.ModuleType("hri.graph.registry")
    registry.get_ingest_graph = lambda: state["graph"]

    llm_client = types.ModuleType("hri.llm.llm_client")
    llm_client.get_usd_krw = lambda: 1400.0
    llm_client.cost_korean_won = lambda usage, usd_to_krw: 1.23

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


def run(env, graph):
    env["graph"] = graph
    return list(env["module"].upload_report("sample.pdf", "user01"))


# ─────────────────────────────────────────────────────────────
# 1. 정상 업로드
# ─────────────────────────────────────────────────────────────
def test_그래프에_입력과_설정을_올바르게_넘김(env):
    graph = FakeGraph(SUCCESS_STEPS)
    run(env, graph)
    call = graph.calls[0]
    assert call["inputs"] == {"file_path": "sample.pdf", "user_id": "user01"}
    assert call["stream_mode"] == "updates"
    assert call["config"]["callbacks"], "사용량 콜백이 config에 들어가야 합니다"


def test_노드마다_진행상황을_내보냄(env):
    out = run(env, FakeGraph(SUCCESS_STEPS))
    steps = [m["step"] for m in out if "step" in m]
    assert steps == [name for name, _ in SUCCESS_STEPS]


def test_마지막_항목에_결과가_있음(env):
    last = run(env, FakeGraph(SUCCESS_STEPS))[-1]
    assert last["report_id"] == 12
    assert last["counts"] == {"text": 157, "table": 8, "chart": 3}


def test_모든_노드의_warnings를_모아서_알림(env):
    last = run(env, FakeGraph(SUCCESS_STEPS))[-1]
    assert last["warnings"] == ["p.2 스캔본 의심", "p.14 차트 수치 검토 필요"]


def test_결과에_메타데이터와_쪽수를_담음(env):
    last = run(env, FakeGraph(SUCCESS_STEPS))[-1]
    assert last["meta"] == META
    assert last["page_count"] == 3


def test_성공하면_모델별로_사용량을_기록(env):
    run(env, FakeGraph(SUCCESS_STEPS, llm_at=["summarize_tables", "process_charts"]))
    assert len(env["usage_calls"]) == 1                       # 모델이 gpt-4o-mini 하나뿐이라 한 줄
    usage = env["usage_calls"][0]
    assert usage.flow == "ingest" and usage.ref_id == 12 and usage.user_id == "user01"
    assert usage.model == "gpt-4o-mini"
    assert usage.input_tokens == 200 and usage.output_tokens == 40   # 100 + 100, 20 + 20 (2번 호출 합계)
    assert usage.cost_krw == 1.23


def test_사용량_기록이_실패해도_업로드_결과는_정상(env):
    env["insert_fails"] = True
    out = run(env, FakeGraph(SUCCESS_STEPS, llm_at=["process_charts"]))   # 예외가 밖으로 나오면 실패
    assert out[-1]["type"] == "result" and out[-1]["report_id"] == 12


# ─────────────────────────────────────────────────────────────
# 2. 실패 상황 (지금 뼈대 코드에서 실패하는 테스트 = 고쳐야 할 점)
# ─────────────────────────────────────────────────────────────
def test_검증_실패해도_에러없이_끝나고_결과를_알림(env):
    out = run(env, FakeGraph(VALIDATE_FAIL_STEPS))          # 예외가 밖으로 나오면 실패
    assert out, "아무것도 내보내지 않았습니다"
    assert "report_id" not in out[-1], "실패인데 report_id가 있습니다"
    assert out[-1] == {"type": "error", "reason": "PDF가 아닙니다"}   # validate의 사유를 그대로 전달


def test_중간_노드_예외도_에러없이_끝나고_결과를_알림(env):
    out = run(env, FakeGraph(SUCCESS_STEPS, raise_at="split"))
    assert out and "report_id" not in out[-1]


def test_실패해도_쓴_AI_비용은_기록(env):
    run(env, FakeGraph(SUCCESS_STEPS, llm_at=["process_charts"], raise_at="split"))
    assert len(env["usage_calls"]) == 1, "AI를 썼는데 비용이 기록되지 않았습니다"
    usage = env["usage_calls"][0]
    assert usage.ref_id is None                    # 저장 전에 실패해 report_id가 없다
    assert usage.flow == "ingest" and usage.user_id == "user01"


def test_AI를_안_썼으면_기록하지_않음(env):
    run(env, FakeGraph(VALIDATE_FAIL_STEPS))
    assert env["usage_calls"] == [], "AI를 안 썼는데 사용량을 기록했습니다"


def test_persist가_report_id를_못_돌려주면_성공으로_보지_않음(env):
    steps = SUCCESS_STEPS[:-1] + [("persist", {})]     # 노드가 아직 뼈대({})인 경우
    last = run(env, FakeGraph(steps))[-1]
    assert last["type"] == "error"
