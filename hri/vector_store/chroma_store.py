"""Chroma 연결·청크 저장 · get_collection, add_chunks, delete_report_vectors (김희진)"""
import chromadb
from chromadb.api.models.Collection import Collection
from hri.config.settings import load_settings
from hri.domain.schemas import Chunk
from hri.vector_store.embedding import embed_texts
import base64, os


BATCH_SIZE = 100 # embed_texts, collection.add를 나눠 호출하는 단위

def basic_auth_header() -> dict[str, str]:
    """CHROMA_USER / CHROMA_PASSWORD 환경변수로 Basic 인증 헤더 생성"""
    credentials = f"{os.environ['CHROMA_USER']}:{os.environ['CHROMA_PASSWORD']}"
    token = base64.b64encode(credentials.encode()).decode()
    return {"Authorization": f"Basic {token}"}

def get_collection() -> Collection:
    """
    보고서 청크 컬렉션을 가져온다. 없으면 코사인 거리로 만든다.
    CHROMA_MODE에 따라 클라이언트를 만든다.
    - http: 서버에 띄운 Chroma에 HttpClient로 연결 (기본값)
    - persistent: 로컬 경로에 저장하는 PersistentClient (테스트, 오프라인용)
    임베딩은 embed_texts()로 직접 만들어 넣으므로 Chroma 기본 임베딩 함수느 쓰지 않는다
    (embedding_function=None)
    """
    settings = load_settings()
    if settings.chroma_mode == "persistent":
        client = chromadb.PersistentClient(path=settings.chroma_persist_path)
    else:
        client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port, ssl=True, headers=basic_auth_header(),)
    return client.get_or_create_collection(name=settings.chroma_collection,
                                           metadata={"hnsw:space": "cosine"},
                                           embedding_function=None)

def add_chunks(chunks: list[Chunk]) -> int:
    """
    청크를 임베딩해 Chroma 컬렉션에 저장하고 저장한 개수를 돌려준다.
    - id: f'{report_id} - {chunk_no}'
    - document: chunk.text (본문, 또는 표, 차트는 캡션+요약)
    - metadata: report_id, page, section_no, chunk_no, chunk_type, category, 표, 차트 청크만 doc_id
      값이 None인 키는 넣지 않는다 (Chroma 버전에 따라 None을 거부함)
    report_id가 None인 청크가 있으면 ValueError (persistent 노드에서 insert_report() 결과로 채운 뒤 호출)
    빈 리스트면 아무것도 하지 않고 0을 반환.
    """
    if not chunks:
        return 0
    if any(chunk.report_id is None for chunk in chunks):
        raise ValueError("report_id가 없는 청크는 저장할 수 없습니다. insert_report() 결과로 채운 뒤 호출하세요.")

    collection = get_collection()
    for start in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[start:start + BATCH_SIZE]
        texts = [chunk.text for chunk in batch]
        collection.add(ids=[f"{chunk.report_id}-{chunk.chunk_no}" for chunk in batch],
                       embeddings=embed_texts(texts),
                       documents=texts,
                       metadatas=[_to_metadata(chunk) for chunk in batch])
    return len(chunks)


def _to_metadata(chunk: Chunk) -> dict:
    """Chunk 1개를 Chroma 메타데이터 dict로 바꾼다. 값이 None인 키는 뺀다"""
    metadata = {
        "report_id": chunk.report_id,
        "page": chunk.page,
        "section_no": chunk.section_no,
        "chunk_no": chunk.chunk_no,
        "chunk_type": chunk.chunk_type,
        "category": chunk.category,
        "doc_id": chunk.doc_id # 텍스트 청크는 None이라 아래에서 빠진다
	}
    return {key: value for key, value in metadata.items() if value is not None}


def delete_report_vectors(report_id: int) -> None:
    """
    해당 보고서의 벡터를 모두 삭제한다.
    persist 노드가 중간에 실패했을 때 rollback과 함께 벡터를 정리하는 데 쓴다.
    지울 벡터가 없어도 오류 없이 끝난다.
    """
    get_collection().delete(where={"report_id": report_id})