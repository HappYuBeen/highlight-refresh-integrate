"""chroma_store 실서버 업로드 테스트 · add_chunks로 올린 데이터가 서버에 실제로 있는지 확인 (김희진)

load_settings만 가짜로 바꿔 test_<uuid> 컬렉션을 쓰게 하고(운영 컬렉션 hri_reports를 건드리지 않고 MySQL 설정도 불필요),
get_collection(), basic_auth_header(), add_chunks()는 실제 코드를 그대로 쓴다.
검증은 chroma_store와 무관한 별도 클라이언트로 서버에서 다시 읽어서 한다.
접속 정보(CHROMA_HOST, CHROMA_USER, CHROMA_PASSWORD)가 없으면 건너뛴다. CHROMA_PORT는 없으면 443.
CHROMA_TEST_KEEP=1 이면 컬렉션을 지우지 않고 이름을 출력한다(pytest -s).
"""
import base64
import os
import uuid
from types import SimpleNamespace

import chromadb
import pytest

from hri.domain.schemas import Chunk
from hri.vector_store import chroma_store
from dotenv import load_dotenv
load_dotenv()


pytestmark = pytest.mark.skipif(
	not (os.getenv("CHROMA_HOST") and os.getenv("CHROMA_USER") and os.getenv("CHROMA_PASSWORD")),
	reason="Chroma 서버 접속 정보(CHROMA_HOST/CHROMA_USER/CHROMA_PASSWORD)가 없어 건너뜀",
)

KEEP = os.getenv("CHROMA_TEST_KEEP") == "1"

# 텍스트 → 3차원 벡터. 서로 직교하므로 코사인 거리 0 / 1이 정확히 나온다
FAKE_VECTORS = {
	"alpha": [1.0, 0.0, 0.0],
	"beta": [0.0, 1.0, 0.0],
	"gamma": [0.0, 0.0, 1.0],
}


def _fake_embed(texts: list[str]) -> list[list[float]]:
	return [FAKE_VECTORS.get(t, [1.0, 1.0, 1.0]) for t in texts]


def _chunk(chunk_no: int, text: str = "alpha", report_id: int | None = 7, **kwargs) -> Chunk:
	return Chunk(
		report_id=report_id,
		chunk_no=chunk_no,
		section_no=kwargs.pop("section_no", 0),
		page=kwargs.pop("page", 1),
		text=text,
		**kwargs,
	)


@pytest.fixture
def verify_client():
	"""chroma_store를 거치지 않는 검증 전용 클라이언트 (인증 헤더도 직접 만든다)."""
	token = base64.b64encode(
		f"{os.environ['CHROMA_USER']}:{os.environ['CHROMA_PASSWORD']}".encode()
	).decode()
	return chromadb.HttpClient(
		host=os.environ["CHROMA_HOST"],
		port=int(os.getenv("CHROMA_PORT", "443")),
		ssl=True,
		headers={"Authorization": f"Basic {token}"},
	)


@pytest.fixture
def collection_name(verify_client, monkeypatch):
	"""get_collection()이 test_<uuid> 컬렉션을 쓰도록 설정만 바꾸고, 끝나면 서버에서 지운다(KEEP이면 남김)."""
	name = f"test_{'keep_' if KEEP else ''}{uuid.uuid4().hex[:12]}"
	fake_settings = SimpleNamespace(
		chroma_mode="http",
		chroma_host=os.environ["CHROMA_HOST"],
		chroma_port=int(os.getenv("CHROMA_PORT", "443")),
		chroma_persist_path="",
		chroma_collection=name,
	)
	monkeypatch.setattr(chroma_store, "load_settings", lambda: fake_settings)
	monkeypatch.setattr(chroma_store, "embed_texts", _fake_embed)
	try:
		yield name
	finally:
		if KEEP:
			print(f"\n[남겨둔 Chroma 컬렉션] {name}")
		else:
			try:
				verify_client.delete_collection(name)
			except Exception:
				pass  # 컬렉션이 만들어지기 전에 실패한 경우


# ---------- 인증 헤더 ----------

def test_basic_auth_header_matches_env(monkeypatch):
	monkeypatch.setenv("CHROMA_USER", "tester")
	monkeypatch.setenv("CHROMA_PASSWORD", "secret")
	expected = base64.b64encode(b"tester:secret").decode()
	assert chroma_store.basic_auth_header() == {"Authorization": f"Basic {expected}"}


# ---------- get_collection ----------

def test_get_collection_creates_collection_on_server(collection_name, verify_client):
	chroma_store.get_collection()
	names = [c.name if hasattr(c, "name") else c for c in verify_client.list_collections()]
	assert collection_name in names


def test_get_collection_uses_cosine_distance(collection_name, verify_client):
	chroma_store.get_collection()
	col = verify_client.get_collection(collection_name)
	assert (col.metadata or {}).get("hnsw:space") == "cosine"


def test_get_collection_called_twice_reuses_collection(collection_name, verify_client):
	chroma_store.get_collection()
	chroma_store.get_collection()
	names = [c.name if hasattr(c, "name") else c for c in verify_client.list_collections()]
	assert names.count(collection_name) == 1


# ---------- add_chunks ----------

def test_add_chunks_data_is_on_server(collection_name, verify_client):
	count = chroma_store.add_chunks([
		_chunk(0, "alpha", page=3, section_no=1, category="경제동향"),
		_chunk(1, "beta", page=4, section_no=1),
	])
	assert count == 2

	# 코드와 별개의 클라이언트로 서버에서 다시 읽는다
	col = verify_client.get_collection(collection_name)
	assert col.count() == 2
	got = col.get(ids=["7-0", "7-1"], include=["documents", "metadatas", "embeddings"])
	by_id = dict(zip(got["ids"], zip(got["documents"], got["metadatas"])))
	doc, meta = by_id["7-0"]
	assert doc == "alpha"
	assert meta == {
		"report_id": 7, "page": 3, "section_no": 1, "chunk_no": 0,
		"chunk_type": "text", "category": "경제동향",
	}
	assert by_id["7-1"][0] == "beta"
	assert len(got["embeddings"][0]) == 3


def test_add_chunks_omits_none_metadata_on_server(collection_name, verify_client):
	chroma_store.add_chunks([_chunk(0, "alpha")])
	meta = verify_client.get_collection(collection_name).get(ids=["7-0"], include=["metadatas"])["metadatas"][0]
	assert "category" not in meta
	assert "doc_id" not in meta


def test_add_chunks_table_chunk_keeps_doc_id_on_server(collection_name, verify_client):
	doc_id = str(uuid.uuid4())
	chroma_store.add_chunks([_chunk(0, "gamma", chunk_type="table", doc_id=doc_id)])
	meta = verify_client.get_collection(collection_name).get(ids=["7-0"], include=["metadatas"])["metadatas"][0]
	assert meta["chunk_type"] == "table"
	assert meta["doc_id"] == doc_id


def test_add_chunks_over_batch_size_all_on_server(collection_name, verify_client):
	# BATCH_SIZE(100) 초과 → 100 + 100 + 50으로 나눠 올라간다
	assert chroma_store.add_chunks([_chunk(i, "alpha") for i in range(250)]) == 250
	assert verify_client.get_collection(collection_name).count() == 250


def test_add_chunks_multiple_reports_stay_separate(collection_name, verify_client):
	chroma_store.add_chunks([_chunk(0, "alpha", report_id=1), _chunk(0, "beta", report_id=2)])
	col = verify_client.get_collection(collection_name)
	assert sorted(col.get()["ids"]) == ["1-0", "2-0"]


def test_add_chunks_empty_list_returns_zero_without_touching_server(collection_name, verify_client):
	assert chroma_store.add_chunks([]) == 0
	# 빈 목록이면 컬렉션도 만들지 않는다
	names = [c.name if hasattr(c, "name") else c for c in verify_client.list_collections()]
	assert collection_name not in names


def test_add_chunks_without_report_id_raises_and_uploads_nothing(collection_name, verify_client):
	with pytest.raises(ValueError):
		chroma_store.add_chunks([_chunk(0, "alpha"), _chunk(1, "beta", report_id=None)])
	names = [c.name if hasattr(c, "name") else c for c in verify_client.list_collections()]
	assert collection_name not in names


# ---------- 검색 (올라간 벡터가 서버에서 실제로 검색되는지) ----------

def test_uploaded_chunks_are_searchable_on_server(collection_name, verify_client):
	chroma_store.add_chunks([_chunk(0, "alpha"), _chunk(1, "beta"), _chunk(2, "gamma")])
	col = verify_client.get_collection(collection_name)

	result = col.query(query_embeddings=[[0.0, 1.0, 0.0]], n_results=3)
	assert result["ids"][0][0] == "7-1"
	assert result["distances"][0][0] == pytest.approx(0.0, abs=1e-6)
	# 직교 벡터의 코사인 거리는 1
	assert result["distances"][0][1] == pytest.approx(1.0, abs=1e-6)


def test_uploaded_chunks_can_be_filtered_by_report_id(collection_name, verify_client):
	chroma_store.add_chunks([_chunk(0, "alpha", report_id=1), _chunk(0, "alpha", report_id=2)])
	col = verify_client.get_collection(collection_name)
	result = col.query(query_embeddings=[[1.0, 0.0, 0.0]], n_results=5, where={"report_id": 2})
	assert result["ids"][0] == ["2-0"]