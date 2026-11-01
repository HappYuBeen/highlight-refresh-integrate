"""embed_texts, similarity 테스트 · 가짜 OpenAI 클라이언트 사용 (김희진)"""
import pytest

from hri.vector_store import embedding


class _FakeItem:
	"""OpenAI 응답의 data 항목 흉내."""

	def __init__(self, index, embedding):
		self.index = index
		self.embedding = embedding


class _FakeEmbeddings:
	"""client.embeddings 흉내. 호출 인자를 기록하고, 텍스트마다 [글자 수, 0.0]을 돌려준다."""

	def __init__(self):
		self.calls = []
		self.reverse = False  # True면 응답 순서를 뒤집어 index 정렬을 확인

	def create(self, model, input):
		self.calls.append({"model": model, "input": list(input)})
		items = [_FakeItem(i, [float(len(text)), 0.0]) for i, text in enumerate(input)]
		if self.reverse:
			items.reverse()
		return type("Response", (), {"data": items})()


class _FakeOpenAI:
	"""OpenAI(api_key=...) 흉내. 만들어진 횟수와 키를 기록한다."""

	instances = []

	def __init__(self, api_key=None):
		self.api_key = api_key
		self.embeddings = _FakeEmbeddings()
		_FakeOpenAI.instances.append(self)


@pytest.fixture
def fake_openai(monkeypatch):
	_FakeOpenAI.instances = []
	monkeypatch.setattr(embedding, "OpenAI", _FakeOpenAI)
	settings = type("S", (), {"openai_api_key": "test-key"})()
	monkeypatch.setattr(embedding, "load_settings", lambda: settings)
	return _FakeOpenAI


# ---------- embed_texts ----------

def test_embed_texts_returns_vectors_in_order(fake_openai):
	vectors = embedding.embed_texts(["a", "bb", "ccc"])
	assert vectors == [[1.0, 0.0], [2.0, 0.0], [3.0, 0.0]]


def test_embed_texts_uses_model_and_key(fake_openai):
	embedding.embed_texts(["a"])
	client = fake_openai.instances[0]
	assert client.api_key == "test-key"
	assert client.embeddings.calls[0]["model"] == "text-embedding-3-small"


def test_embed_texts_empty_skips_api(fake_openai):
	assert embedding.embed_texts([]) == []
	assert fake_openai.instances == []  # 클라이언트도 만들지 않는다


def test_embed_texts_batches_by_100(fake_openai):
	texts = ["x"] * 250
	vectors = embedding.embed_texts(texts)
	client = fake_openai.instances[0]
	assert [len(c["input"]) for c in client.embeddings.calls] == [100, 100, 50]
	assert len(vectors) == 250
	assert len(fake_openai.instances) == 1  # 배치마다 새로 만들지 않는다


def test_embed_texts_sorts_by_index(fake_openai, monkeypatch):
	# 응답이 뒤집혀 와도 입력 순서대로 돌려준다
	orig_init = _FakeOpenAI.__init__

	def init(self, api_key=None):
		orig_init(self, api_key)
		self.embeddings.reverse = True

	monkeypatch.setattr(_FakeOpenAI, "__init__", init)
	assert embedding.embed_texts(["a", "bb", "ccc"]) == [[1.0, 0.0], [2.0, 0.0], [3.0, 0.0]]


# ---------- similarity ----------

def test_similarity_same_direction():
	assert embedding.similarity([1.0, 2.0], [2.0, 4.0]) == pytest.approx(1.0)


def test_similarity_orthogonal():
	assert embedding.similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_similarity_opposite():
	assert embedding.similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_similarity_known_value():
	# cos = (1*1 + 1*0) / (sqrt(2) * 1)
	assert embedding.similarity([1.0, 1.0], [1.0, 0.0]) == pytest.approx(2 ** -0.5)


def test_similarity_zero_vector_returns_zero():
	assert embedding.similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


def test_similarity_length_mismatch_raises():
	with pytest.raises(ValueError):
		embedding.similarity([1.0, 2.0], [1.0, 2.0, 3.0])


def test_similarity_returns_python_float():
	assert isinstance(embedding.similarity([1.0, 2.0], [3.0, 4.0]), float)