"""vector_search, safe_search 로직 테스트 · 가짜 Chroma 컬렉션 사용 (장유빈)"""
import pytest

from hri.retriever import search


class _FakeCollection:
	"""Chroma 컬렉션 흉내. query 호출 인자를 기록하고 고정 결과를 돌려준다."""

	def __init__(self):
		self.kwargs = None

	def query(self, **kwargs):
		self.kwargs = kwargs
		base = {"report_id": 1, "section_no": 1, "page": 1}
		return {
			"documents": [["본문 A", "표 B", "본문 C"]],
			"metadatas": [[
				{**base, "chunk_no": 1, "chunk_type": "text"},
				{**base, "chunk_no": 2, "chunk_type": "table", "category": "금리"},
				{**base, "chunk_no": 3},
			]],
			"distances": [[0.1, 0.5, 0.9]],
			"embeddings": [[[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]],
		}


@pytest.fixture
def fake(monkeypatch):
	collection = _FakeCollection()
	monkeypatch.setattr(search, "get_collection", lambda: collection)
	return collection


def test_vector_search_converts_hits(fake):
	hits = search.vector_search([0.0, 0.0], k=3)
	assert len(hits) == 3
	assert hits[0].score == pytest.approx(0.9)
	assert hits[1].chunk.chunk_type == "table"
	assert hits[1].chunk.category == "금리"
	assert hits[2].chunk.chunk_type == "text"


def test_vector_search_filter(fake):
	search.vector_search([0.0, 0.0], k=3, report_ids=[1, 2])
	assert fake.kwargs["where"] == {"report_id": {"$in": [1, 2]}}


@pytest.mark.parametrize("ids", [None, []])
def test_vector_search_no_filter(fake, ids):
	search.vector_search([0.0, 0.0], k=3, report_ids=ids)
	assert fake.kwargs["where"] is None


def test_safe_search_threshold(fake):
	hits = search.safe_search([0.0, 0.0], k=3)
	# score 0.9, 0.5, 0.1 중 threshold 0.3 이상만 남는다
	assert [h.chunk.chunk_no for h in hits] == [1, 2]