"""임베딩 · embed_texts, similarity (김희진)"""
import numpy as np
from openai import OpenAI
from hri.config.settings import load_settings

EMBEDDING_MODEL = "text-embedding-3-large"
BATCH_SIZE = 100 # 한 번에 보내는 텍스트 수

def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    텍스트 목록을 임베딩 벡터 목록으로 변환.
    OpenAI embeddings API를 BATCH_SIZE개씩 나눠 호출하고,
    입력과 같은 순서로 벡터를 돌려준다. 빈 리스트면 API를 부르지 않고 []를 돌려준다.
    청크 저장(add_chunks)과 검색(hybrid_search)이 함께 쓴다.
    """
    if not texts:
        return []

    client = OpenAI(api_key=load_settings().openai_api_key)
    vectors: list[list[float]] = []

    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start:start+BATCH_SIZE]
        response = client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        # 응답 순서는 index 순이지만 확실히 하기위해 정렬
        vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
    
    return vectors

def similarity(v1: list[float], v2: list[float]) -> float:
    """
    두 벡터의 코사인 유사도를 계산한다. 값은 -1 ~ 1이고 클수록 비슷.
    mmr_select()와 검색 품질 테스트에서 사용. 길이가 다르면 ValueError,
    둘 중 하나가 영벡터(길이 0)면 0.0을 반환.
    """
    a = np.asarray(v1, dtype=float)
    b = np.asarray(v2, dtype=float)
    if a.shape != b.shape:
        raise ValueError(f"벡터 길이가 다릅니다: {a.shape[0]}, {b.shape[0]}")

    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(a @ b / denom)