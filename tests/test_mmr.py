"""mmr_select 로직 테스트 (장유빈)"""
from hri.domain.schemas.qa import SearchHit
from hri.domain.schemas.report import Chunk
from hri.retriever.search import mmr_select


def _hit(chunk_no: int, embedding: list[float]) -> SearchHit:
	chunk = Chunk(
		report_id=1, chunk_no=chunk_no, section_no=1, page=1,
		chunk_type="text", category=None, text="본문", doc_id=None,
	)
	return SearchHit(chunk=chunk, score=0.5, embedding=embedding)


QUERY = [1.0, 0.0]
# A와 B는 거의 같은 내용(벡터가 비슷함), C는 A와 다른 방향
A = _hit(1, [0.9, 0.4359])
B = _hit(2, [0.89, 0.4560])
C = _hit(3, [0.8, -0.6])


def test_skips_duplicate_and_picks_diverse():
	result = mmr_select(QUERY, [A, B, C], k=2)
	assert [h.chunk.chunk_no for h in result] == [1, 3]


def test_lambda_one_picks_by_relevance():
	result = mmr_select(QUERY, [A, B, C], k=2, lambda_mult=1.0)
	assert [h.chunk.chunk_no for h in result] == [1, 2]


def test_k_larger_than_candidates():
	result = mmr_select(QUERY, [A, B], k=5)
	assert len(result) == 2


def test_empty_candidates_or_zero_k():
	assert mmr_select(QUERY, [], k=3) == []
	assert mmr_select(QUERY, [A, B, C], k=0) == []