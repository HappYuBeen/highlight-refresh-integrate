"""대화 기록 체크포인터 · get_checkpointer (엄정현)

qa_graph에 붙여 질문·답변 상태를 thread_id(=session_id)별로 보관한다.
1차는 메모리 저장(InMemorySaver)이라 서버를 다시 켜면 사라지고, 그때는 restore_session()이 qa_logs에서 복원한다.
운영 단계에서는 DB 기반 체크포인터(langgraph-checkpoint-sqlite 등)로 교체를 검토한다.
"""

from functools import lru_cache

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver


@lru_cache(maxsize=1)
def get_checkpointer() -> BaseCheckpointSaver:
	"""InMemorySaver를 하나만 만들어 돌려준다 (두 번째 호출부터는 같은 객체).

	qa_graph만 이 체크포인터로 compile한다 (registry.get_qa_graph()).
	테스트에서 새 저장소가 필요하면 get_checkpointer.cache_clear()를 부른다.
	"""
	return InMemorySaver()
