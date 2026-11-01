"""그래프 지연 싱글턴 · get_ingest_graph, get_qa_graph, get_summary_graph, get_agent_graph (엄정현)

서비스(upload_report·ask_stream·summarize_report·run_agent_with_tools)와 화면(main 예열)은
그래프를 직접 만들지 않고 모두 여기서 받는다.

- @functools.lru_cache: 처음 부를 때 한 번만 build_*_graph()로 만들고 compile한다. 이후에는 같은 그래프를 돌려준다.
- build 함수 import는 함수 안에서 한다.
  노드가 서비스 함수(validate_pdf 등)를 부르고, 서비스(document_service 등)가 이 파일을 import하므로
  파일 맨 위에서 import하면 graph ↔ service 순환 import가 생긴다.
- 테스트에서 가짜 모델로 바꾼 뒤 다시 만들려면 get_*_graph.cache_clear()를 부른다.

사용: from hri.graph.registry import get_ingest_graph
"""

from functools import lru_cache

from langgraph.graph.state import CompiledStateGraph


@lru_cache(maxsize=1)
def get_ingest_graph() -> CompiledStateGraph:
	"""보고서 업로드 그래프 (upload_report()가 stream으로 실행)."""
	from hri.graph.ingest_graph import build_ingest_graph

	return build_ingest_graph().compile()


@lru_cache(maxsize=1)
def get_qa_graph() -> CompiledStateGraph:
	"""질의응답 그래프 (ask_stream()이 stream으로 실행). 체크포인터로 thread_id별 대화 상태를 보관한다."""
	from hri.graph.checkpointer import get_checkpointer
	from hri.graph.qa_graph import build_qa_graph

	return build_qa_graph().compile(checkpointer=get_checkpointer())


@lru_cache(maxsize=1)
def get_summary_graph() -> CompiledStateGraph:
	"""보고서 요약 그래프 (summarize_report()가 invoke로 실행)."""
	from hri.graph.summary_graph import build_summary_graph

	return build_summary_graph().compile()


@lru_cache(maxsize=1)
def get_agent_graph() -> CompiledStateGraph:
	"""Tool Calling 그래프 (run_agent_with_tools()가 invoke로 실행)."""
	from hri.graph.agent_graph import build_agent_graph

	return build_agent_graph().compile()
