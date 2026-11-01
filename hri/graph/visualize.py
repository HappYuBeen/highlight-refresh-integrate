"""그래프 구조도 문서화 · draw_graphs (엄정현)

4개 그래프의 노드·엣지를 Mermaid 그림으로 저장해 리뷰·결과보고서(설계 장)에 쓴다.
노드가 모두 채워진 뒤(3.8.9 그래프 테스트 다음)에 레포 맨 위 폴더에서 실행한다.

실행: poetry run python -c "from hri.graph.visualize import draw_graphs; print(draw_graphs())"
"""

import os

from hri.graph.registry import get_agent_graph, get_ingest_graph, get_qa_graph, get_summary_graph

# 파일 이름(그래프 이름) → 그래프를 돌려주는 함수
GRAPHS = {
	"ingest": get_ingest_graph,
	"qa": get_qa_graph,
	"summary": get_summary_graph,
	"agent": get_agent_graph,
}


def draw_graphs(out_dir: str = "docs/graphs") -> list[str]:
	"""4개 그래프의 get_graph().draw_mermaid()를 <out_dir>/<그래프명>.md로 저장하고 파일 경로 목록을 돌려준다.

	예) draw_graphs() → ['docs/graphs/ingest.md', 'docs/graphs/qa.md', 'docs/graphs/summary.md', 'docs/graphs/agent.md']
	"""
	os.makedirs(out_dir, exist_ok=True)
	paths = []
	for name, get_graph in GRAPHS.items():
		mermaid = get_graph().get_graph().draw_mermaid()
		path = os.path.join(out_dir, f"{name}.md")
		# ```mermaid 블록으로 감싸면 GitHub에서 그림으로 보인다
		with open(path, "w", encoding="utf-8") as f:
			f.write(f"# {name} 그래프\n\n```mermaid\n{mermaid}```\n")
		paths.append(path)
	return paths
