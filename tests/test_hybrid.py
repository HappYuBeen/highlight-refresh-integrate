"""hybrid_search 로직 테스트 · 검색·임베딩 함수를 가짜로 바꿔 확인 (장유빈)"""
import pytest

from hri.domain.schemas.qa import SearchHit
from hri.domain.schemas.report import Chunk
from hri.retriever import search

A, B, C = 1, 2, 3  # chunk_no


def _chunk(no: int) -> Chunk:
	return Chunk(
		report_id=1, chunk_no=no, section_no=1, page=1,
		chunk_type="text", category=None, text=f"청크{no}", doc_id=None,
	)


def _hit(no: int) -> SearchHit:
	return SearchHit(chunk=_chunk(no), score=0.9, embedding=[0.1, 0.2])


@pytest.fixture
def calls(monkeypatch):
	"""검색 함수를 가짜로 바꾸고, safe_search·mmr_select가 받은 k를 기록한다."""
	record = {"safe_k": [], "mmr_k": []}
	# 질문 벡터의 첫 값(1.0, 2.0)으로 질문마다 다른 의미 검색 결과를 돌려준다
	semantic = {1.0: [_hit(B), _hit(C)], 2.0: [_hit(B), _hit(A)]}

	monkeypatch.setattr(search, "extract_keywords", lambda question: ["키워드"])
	monkeypatch.setattr(search, "keyword_find", lambda keywords, report_ids=None: [_chunk(A), _chunk(B)])
	monkeypatch.setattr(search, "expand_queries", lambda question: ["확장 질문"])
	monkeypatch.setattr(search, "embed_texts", lambda texts: [[1.0], [2.0]])

	def fake_safe_search(query_vec, k, report_ids=None):
		record["safe_k"].append(k)
		return semantic[query_vec[0]]

	def fake_mmr_select(query_vec, candidates, k, lambda_mult=0.5):
		record["mmr_k"].append(k)
		return candidates[:k]

	monkeypatch.setattr(search, "safe_search", fake_safe_search)
	monkeypatch.setattr(search, "mmr_select", fake_mmr_select)
	return record


def test_rrf_merge_order(calls):
	# 점수: B = 1/62 + 1/61 + 1/61 (가장 높음), A = 1/61 + 1/62, C = 1/62
	result = search.hybrid_search("질문", k=3)
	assert [chunk.chunk_no for chunk in result] == [B, A, C]


def test_top_k_only(calls):
	result = search.hybrid_search("질문", k=2)
	assert [chunk.chunk_no for chunk in result] == [B, A]


def test_no_semantic_results_returns_empty(calls, monkeypatch):
	monkeypatch.setattr(search, "safe_search", lambda query_vec, k, report_ids=None: [])
	# 키워드 결과(A, B)가 있어도 의미 검색이 0건이면 근거 없음
	assert search.hybrid_search("질문", k=3) == []


def test_without_mmr_uses_k(calls):
	search.hybrid_search("질문", k=2)
	assert calls["safe_k"] == [2, 2]
	assert calls["mmr_k"] == []


def test_with_mmr_fetches_20_then_selects_k(calls):
	search.hybrid_search("질문", k=2, use_mmr=True)
	assert calls["safe_k"] == [20, 20]
	assert calls["mmr_k"] == [2, 2]


def test_empty_question(calls):
	assert search.hybrid_search("   ") == []