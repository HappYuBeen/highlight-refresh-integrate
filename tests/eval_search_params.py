"""검색 설정 비교 — MMR · 근거 수(k) · CHUNK_SIZE · CHUNK_OVERLAP · THRESHOLD (서동안, 4.1.8 테스트 결과서용)

검색 설정을 바꿔 가며 같은 질문(보고서 안 30문항 + 보고서 밖 16문항)으로 결과를 비교하고, 최적 설정을 고른다.

1단계 (검색만, 기본) — 설정 조합 전부 (기본 12 청크 설정 × k 10 × MMR 2 × THRESHOLD 11 = 2,640개)
	답변 LLM은 부르지 않고 hybrid_search() 결과만 본다. 질문 확장(LLM)·임베딩은 한 번만 만들어 저장해 두고 재사용한다.
	지표: 근거 쪽 적중률 · 1순위 적중률 · 근거 없음 오판율 · 보고서 밖 차단율 · 근거 문서 수 · 컨텍스트 글자 수
2단계 (답변까지, --answer-top N 또는 --answer 설정) — 1단계 상위 설정 + 현재 설정만
	실제로 답변을 만들어 정확도(자동 후보)·출처 일치·응답 시간·원화 비용을 잰다 (eval_qa_accuracy.run_question 사용).

팀 DB·Chroma는 읽기만 한다
	- 섹션 원문(sections), 표·차트 청크(chunks), 표 원본 길이(report_tables)를 SELECT로 읽는다.
	- CHUNK_SIZE·CHUNK_OVERLAP별 텍스트 청크는 섹션 원문을 split_text()로 다시 나눠 이 컴퓨터 메모리에서 검색한다.
	  (업로드 때 split_node와 같은 방식: 섹션마다 split_text, 청크 쪽 = 섹션 시작 쪽, 표·차트 청크는 그대로)
	- 그래서 보고서를 다시 업로드하거나 DB·Chroma에 쓰지 않는다.
	- 검색 순서·RRF·MMR·THRESHOLD 거르기는 실제 hybrid_search()·mmr_select()·safe_search() 코드를 그대로 쓰고,
	  저장소(Chroma·MySQL LIKE)만 메모리 검색으로 바꿔 끼운다.

최적 설정을 고르는 규칙 (결과서 '검색 설정 비교' 시트와 같은 규칙)
	1) 보고서 안 질문의 근거 없음 오판율 ≤ 10%  (= 근거 찾음 90% 이상, 4.1.2 하한과 같음)
	2) 그중 근거 쪽 적중률이 최고값에서 1문항(1/30) 이내인 설정  (1문항 차이는 우연일 수 있어서)
	3) 그중 보고서 밖 질문 차단율이 가장 높은 설정
	4) 그래도 같으면 컨텍스트 글자 수가 가장 적은 설정  (LLM 입력이 짧을수록 비용·응답 시간이 줄어듦)
	2단계: 정확도(자동 후보)가 가장 높은 설정, 차이가 0.05 이내면 문항당 비용이 낮은 설정

실행 (프로젝트 루트, .env·SSH 터널 필요. 결과는 logs/eval_search/)
	poetry run python -m tests.eval_search_params --dry-run          # report_id·청크 수만 확인 (API 호출 없음)
	poetry run python -m tests.eval_search_params                    # 1단계 (비용: 질문 확장 46회 + 임베딩, 수십 원 수준)
	poetry run python -m tests.eval_search_params --answer-top 3     # 1단계 + 상위 3개·현재 설정 답변 비교 (30문항 × 4설정)
	poetry run python -m tests.eval_search_params --skip-grid --answer 500/50/4/off/0.30 800/100/6/on/0.35
옵션
	--sizes 300,500,800,1000  --overlaps 0,50,100  --ks 1-10  --mmr both|on|off  --thresholds 0.10:0.60:0.05
	--report-ids FX=3,PR=4,GR=5  --persona 실무자
리포트 (마크다운)
	search_grid_<시각>_리포트.md    : 지표 계산식 → 추천 설정을 고른 규칙 1~4의 단계별 개수 → 추천·현재 설정의 질문별
	                                  기대(정답 쪽 / 근거 없음)·실제 근거 문서·적중 계산 → 현재 설정에서 하나씩 바꾼 비교표
	search_answers_<시각>_리포트.md : 설정별 결과 → 최종 추천 계산 → 설정마다 문항별 질문·기대 답변·실제 답변·채점 근거
임베딩(text-embedding-3-large)·확장 질문 캐시는 모델 이름별 파일로 logs/eval_search/에 저장한다.
임베딩 모델(text-embedding-3-large)로 바뀌며 점수 분포가 달라졌을 수 있어 THRESHOLD 기본 범위를 0.10부터로 넓혔다.
"""

import argparse
import csv
import hashlib
import json
import statistics
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from tests.eval_report import FAIL, PASS, mark, mean_formula, table, write_report

DEFAULT_ANSWERS = Path("tests/fixtures/qa_answers.json")
DEFAULT_OUT_OF_SCOPE = Path("tests/fixtures/qa_out_of_scope.json")
DEFAULT_OUT_DIR = Path("logs/eval_search")
CANDIDATES = 20  # 의미 검색 후보를 미리 받아 두는 개수 (search.MMR_FETCH_K와 같음, k 최대 10보다 큼)

# 최적 설정 규칙의 기준값
MAX_MISS_RATE = 0.10  # 근거 없음 오판율 상한
HIT_TOLERANCE_QUESTIONS = 1  # 적중률 최고값과 몇 문항 차이까지 같은 수준으로 볼지
ACCURACY_TOLERANCE = 0.05  # 2단계 정확도 차이 허용

GRID_COLUMNS = ["설정", "CHUNK_SIZE", "CHUNK_OVERLAP", "텍스트 청크 수", "k", "MMR", "THRESHOLD",
	"안 질문 수", "근거 쪽 적중", "근거 쪽 적중률", "1순위 적중률", "근거 없음 오판", "근거 없음 오판율",
	"밖 질문 수", "밖 질문 차단", "밖 질문 차단율", "평균 근거 문서 수", "평균 컨텍스트 글자 수"]
ANSWER_COLUMNS = ["설정", "번호", "질문", "route", "응답시간(ms)", "토큰", "비용(원)",
	"정확도(자동 후보)", "출처 일치", "응답 출처 쪽", "답변"]
ANSWER_SUMMARY_COLUMNS = ["설정", "CHUNK_SIZE", "CHUNK_OVERLAP", "k", "MMR", "THRESHOLD", "문항 수",
	"정확도(자동 후보)", "출처 일치율", "근거 없음", "평균 응답시간(ms)", "P95 응답시간(ms)", "문항당 비용(원)", "총 비용(원)"]


# =====================================================================
# 1. 설정 표기
# =====================================================================
def config_name(size, overlap, k, mmr, threshold) -> str:
	return f"{size}/{overlap}/{k}/{'on' if mmr else 'off'}/{threshold:.2f}"


def parse_config(text: str) -> tuple[int, int, int, bool, float]:
	"""'500/50/4/off/0.30' → (500, 50, 4, False, 0.3)"""
	size, overlap, k, mmr, threshold = text.split("/")
	return int(size), int(overlap), int(k), mmr.lower() in ("on", "1", "true", "mmr"), float(threshold)


def parse_range(text: str) -> list[int]:
	"""'1-10' 또는 '1,2,4,8'"""
	if "-" in text:
		start, end = text.split("-")
		return list(range(int(start), int(end) + 1))
	return [int(x) for x in text.split(",")]


def parse_thresholds(text: str) -> list[float]:
	"""'0.20:0.60:0.05' 또는 '0.2,0.3'"""
	if ":" in text:
		start, end, step = (float(x) for x in text.split(":"))
		count = int(round((end - start) / step)) + 1
		return [round(start + step * i, 4) for i in range(count)]
	return [float(x) for x in text.split(",")]


# =====================================================================
# 2. 팀 DB에서 읽기 (SELECT만)
# =====================================================================
def load_report_data(report_ids: list[int]) -> dict:
	"""섹션 원문, 표·차트 청크, 표 원본 길이를 읽는다."""
	from hri.dao.db import get_connection
	from hri.dao.report_dao import select_sections

	sections = {rid: select_sections(rid) for rid in report_ids}
	marks = ", ".join(["%s"] * len(report_ids))
	with get_connection() as conn:
		with conn.cursor() as cur:
			cur.execute(f"SELECT report_id, chunk_no, section_no, page, chunk_type, category, text, doc_id "
				f"FROM chunks WHERE chunk_type <> 'text' AND report_id IN ({marks}) ORDER BY report_id, chunk_no", report_ids)
			table_chunks = list(cur.fetchall())
			cur.execute(f"SELECT report_id, COUNT(*) AS n FROM chunks WHERE chunk_type = 'text' AND report_id IN ({marks}) "
				"GROUP BY report_id", report_ids)
			text_counts = {row["report_id"]: row["n"] for row in cur.fetchall()}
			cur.execute(f"SELECT doc_id, CHAR_LENGTH(markdown) AS n FROM report_tables WHERE report_id IN ({marks})", report_ids)
			table_lengths = {row["doc_id"]: row["n"] for row in cur.fetchall()}
	return {"sections": sections, "table_chunks": table_chunks, "db_text_counts": text_counts, "table_lengths": table_lengths}


def build_chunks(report_data: dict, size: int, overlap: int) -> list:
	"""split_node와 같은 방식으로 청크를 만든다 (텍스트 청크 → 표·차트 청크 순, chunk_no는 보고서마다 0부터)."""
	from hri.domain.schemas import Chunk
	from hri.splitter.text_splitter import split_text

	chunks = []
	for rid, sections in report_data["sections"].items():
		local = []
		for section in sections:
			for piece in split_text(section.text, size, overlap):
				local.append(Chunk(report_id=rid, chunk_no=len(local), section_no=section.section_no,
					page=section.start_page, chunk_type="text", text=piece))
		for row in (r for r in report_data["table_chunks"] if r["report_id"] == rid):
			local.append(Chunk(**{**row, "chunk_no": len(local)}))
		chunks.extend(local)
	return chunks


# =====================================================================
# 3. 임베딩·질문 확장 캐시 (다시 돌릴 때 API를 또 부르지 않음)
# =====================================================================
class EmbeddingCache:
	def __init__(self, path: Path):
		self.path = path
		self.vectors: dict[str, list[float]] = {}
		if path.exists():
			data = np.load(path, allow_pickle=False)
			for key, row in zip(data["keys"], data["matrix"]):
				self.vectors[str(key)] = row.tolist()

	@staticmethod
	def key(text: str) -> str:
		return hashlib.sha1(text.encode("utf-8")).hexdigest()

	def get(self, texts: list[str], embed) -> list[list[float]]:
		missing = list(dict.fromkeys(t for t in texts if self.key(t) not in self.vectors))
		if missing:
			for text, vec in zip(missing, embed(missing)):
				self.vectors[self.key(text)] = list(vec)
			self.save()
		return [self.vectors[self.key(t)] for t in texts]

	def save(self) -> None:
		self.path.parent.mkdir(parents=True, exist_ok=True)
		keys = list(self.vectors)
		np.savez(self.path, keys=np.array(keys), matrix=np.array([self.vectors[k] for k in keys], dtype=np.float64))


def load_expansions(path: Path, questions: list[str], expand) -> dict[str, list[str]]:
	cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
	for question in questions:
		if question not in cache:
			cache[question] = expand(question)
			path.parent.mkdir(parents=True, exist_ok=True)
			path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
	return cache


# =====================================================================
# 4. 메모리 검색 저장소 (Chroma·MySQL LIKE 대신)
# =====================================================================
class MemoryIndex:
	"""한 청크 설정의 청크와 임베딩. 의미 검색은 코사인 유사도 정확 계산, 키워드 검색은 LIKE와 같은 포함 검사."""

	def __init__(self, chunks: list, vectors: list[list[float]]):
		self.chunks = chunks
		self.vectors = vectors  # mmr_select가 쓰는 hit.embedding (같은 리스트 객체를 계속 씀)
		matrix = np.asarray(vectors, dtype=np.float64)
		self.matrix = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)
		self.report_of = np.array([c.report_id for c in chunks])
		self.lower = [c.text.casefold() for c in chunks]

	def vector_search(self, query_vec, k: int, report_ids) -> list:
		from hri.domain.schemas.qa import SearchHit

		q = np.asarray(query_vec, dtype=np.float64)
		q = q / np.linalg.norm(q)
		scores = self.matrix @ q
		idx = np.arange(len(self.chunks))
		if report_ids:
			idx = idx[np.isin(self.report_of, list(report_ids))]
		top = idx[np.argsort(-scores[idx], kind="stable")[:k]]
		return [SearchHit.model_construct(chunk=self.chunks[i], score=float(scores[i]), embedding=self.vectors[i]) for i in top]

	def keyword_find(self, keywords: list[str], report_ids=None) -> list:
		"""search.keyword_find와 같은 결과 순서: 맞은 키워드 수 많은 순 → report_id → chunk_no (대소문자 무시)."""
		if not keywords:
			return []
		words = [w.casefold() for w in keywords]
		found = []
		for chunk, text in zip(self.chunks, self.lower):
			if report_ids and chunk.report_id not in report_ids:
				continue
			count = sum(w in text for w in words)
			if count:
				found.append((-count, chunk.report_id, chunk.chunk_no, chunk))
		return [item[3] for item in sorted(found, key=lambda x: x[:3])]


class FastSimilarity:
	"""mmr_select가 같은 벡터 쌍을 여러 번 비교하므로 정규화 벡터·결과를 기억해 둔다 (값은 similarity()와 같음)."""

	def __init__(self, original):
		self.original = original
		self.unit: dict[int, np.ndarray] = {}
		self.pairs: dict[tuple[int, int], float] = {}

	def register(self, vec) -> None:
		a = np.asarray(vec, dtype=np.float64)
		self.unit[id(vec)] = a / np.linalg.norm(a)

	def __call__(self, v1, v2) -> float:
		key = (id(v1), id(v2))
		if key not in self.pairs:
			if id(v1) in self.unit and id(v2) in self.unit:
				self.pairs[key] = float(self.unit[id(v1)] @ self.unit[id(v2)])
			else:
				return self.original(v1, v2)
		return self.pairs[key]


@contextmanager
def patched_search(**replacements):
	"""hri.retriever.search 모듈의 함수를 잠시 바꿔 끼운다 (끝나면 원래대로)."""
	from hri.retriever import search

	saved = {name: getattr(search, name) for name in replacements}
	try:
		for name, value in replacements.items():
			setattr(search, name, value)
		yield search
	finally:
		for name, value in saved.items():
			setattr(search, name, value)


# =====================================================================
# 5. 지표 계산
# =====================================================================
def context_docs(chunks: list, sections: dict, table_lengths: dict, max_chars) -> list[tuple]:
	"""get_context_docs()와 같은 규칙으로 근거 문서를 만든다: (report_id, 시작 쪽, 끝 쪽, 글자 수). 같은 섹션·표는 1번만."""
	docs, seen = [], set()
	for chunk in chunks:
		if chunk.chunk_type == "text":
			key = ("s", chunk.report_id, chunk.section_no)
			section = sections.get((chunk.report_id, chunk.section_no))
			if section is None or key in seen:
				continue
			seen.add(key)
			length = len(section.text) if max_chars is None else min(len(section.text), max_chars)
			docs.append((chunk.report_id, section.start_page, section.end_page, length))
		else:
			key = ("t", chunk.doc_id)
			if chunk.doc_id not in table_lengths or key in seen:
				continue
			seen.add(key)
			docs.append((chunk.report_id, chunk.page, chunk.page, table_lengths[chunk.doc_id] or 0))
	return docs


def gold_hit(docs: list[tuple], expected: dict[str, list[int]], id_of: dict[str, int]) -> bool:
	"""정답 보고서마다 근거 문서 중 하나라도 정답 쪽을 포함하면 적중 (eval_qa_accuracy.source_match와 같은 기준)."""
	return all(
		any(doc[0] == id_of[code] and any(doc[1] <= p <= doc[2] for p in pages) for doc in docs)
		for code, pages in expected.items()
	)


def first_hit(docs: list[tuple], expected: dict[str, list[int]], id_of: dict[str, int]) -> bool:
	"""첫 번째 근거 문서가 정답 쪽 중 하나를 포함하는지 (k가 작을 때 차이를 보려고)."""
	if not docs:
		return False
	doc = docs[0]
	return any(doc[0] == id_of[code] and any(doc[1] <= p <= doc[2] for p in pages) for code, pages in expected.items())


# =====================================================================
# 6. 1단계: 검색 설정 조합 전부
# =====================================================================
def run_grid(args, data, oos, id_of, report_data, emb_cache, expansions, settings) -> list[dict]:
	from hri.retriever import search
	from hri.vector_store.embedding import embed_texts, similarity

	in_items = data["items"]
	out_items = oos["items"]
	questions = [(it, True) for it in in_items] + [(it, False) for it in out_items]
	sections = {(s.report_id, s.section_no): s for secs in report_data["sections"].values() for s in secs}

	# 질문 + 확장 질문 벡터 (질문마다 같은 리스트 객체를 계속 써서 id로 찾을 수 있게 함)
	query_texts = {it["question"]: [it["question"], *expansions[it["question"]]] for it, _ in questions}
	all_texts = [t for texts in query_texts.values() for t in texts]
	query_vecs = dict(zip(all_texts, emb_cache.get(all_texts, embed_texts)))
	fast = FastSimilarity(similarity)
	for vec in query_vecs.values():
		fast.register(vec)

	rows = []
	configs = [(s, o) for s in args.sizes for o in args.overlaps if o < s]
	mmr_options = {"both": [False, True], "on": [True], "off": [False]}[args.mmr]
	total = len(configs) * len(args.ks) * len(mmr_options) * len(args.thresholds)
	print(f"[1단계] 청크 설정 {len(configs)}개 × k {len(args.ks)}개 × MMR {len(mmr_options)}개 × THRESHOLD {len(args.thresholds)}개 = {total}개 조합")

	for size, overlap in configs:
		started = time.perf_counter()
		chunks = build_chunks(report_data, size, overlap)
		vectors = emb_cache.get([c.text for c in chunks], embed_texts)
		index = MemoryIndex(chunks, vectors)
		for vec in index.vectors:
			fast.register(vec)
		text_count = sum(c.chunk_type == "text" for c in chunks)

		# 질문 벡터마다 상위 후보를 미리 계산해 두고 k만큼 잘라 쓴다 (k개 검색 = 후보 20개의 앞 k개)
		top: dict[tuple, list] = {}
		for it, _ in questions:
			rids = tuple(id_of[c] for c in it["reports"])
			for text in query_texts[it["question"]]:
				top[(id(query_vecs[text]), rids)] = index.vector_search(query_vecs[text], CANDIDATES, rids)

		# MMR은 앞에서부터 하나씩 고르므로 k개 결과 = 전부 고른 순서의 앞 k개 → 후보 묶음마다 한 번만 계산
		mmr_memo: dict[tuple, list] = {}
		real_mmr = search.mmr_select

		def memo_mmr(query_vec, candidates, k, lambda_mult=0.5):
			key = (id(query_vec), tuple(id(h) for h in candidates), lambda_mult)
			if key not in mmr_memo:
				mmr_memo[key] = real_mmr(query_vec, candidates, len(candidates), lambda_mult)
			return mmr_memo[key][:k]

		def fake_vector_search(query_vec, k=4, report_ids=None):
			return top[(id(query_vec), tuple(report_ids or ()))][:k]

		for threshold in args.thresholds:
			replacements = dict(
				load_settings=lambda t=threshold: SimpleNamespace(threshold=t),
				vector_search=fake_vector_search,
				keyword_find=index.keyword_find,
				expand_queries=lambda q: expansions[q],
				embed_texts=lambda texts: [query_vecs[t] for t in texts],
				similarity=fast,
				mmr_select=memo_mmr,
			)
			with patched_search(**replacements) as s:
				for k in args.ks:
					for mmr in mmr_options:
						hits = first = miss = blocked = 0
						doc_counts, ctx_chars = [], []
						for it, inside in questions:
							rids = [id_of[c] for c in it["reports"]]
							found = s.hybrid_search(it["question"], k=k, report_ids=rids, use_mmr=mmr)
							if inside:
								if not found:
									miss += 1
									continue
								docs = context_docs(found, sections, report_data["table_lengths"], settings.max_context_chars)
								hits += gold_hit(docs, it["pages"], id_of)
								first += first_hit(docs, it["pages"], id_of)
								doc_counts.append(len(docs))
								ctx_chars.append(sum(d[3] for d in docs))
							else:
								blocked += not found
						n_in, n_out = len(in_items), len(out_items)
						rows.append({
							"설정": config_name(size, overlap, k, mmr, threshold), "CHUNK_SIZE": size, "CHUNK_OVERLAP": overlap,
							"텍스트 청크 수": text_count, "k": k, "MMR": "on" if mmr else "off", "THRESHOLD": threshold,
							"안 질문 수": n_in, "근거 쪽 적중": hits, "근거 쪽 적중률": round(hits / n_in, 4),
							"1순위 적중률": round(first / n_in, 4), "근거 없음 오판": miss, "근거 없음 오판율": round(miss / n_in, 4),
							"밖 질문 수": n_out, "밖 질문 차단": blocked, "밖 질문 차단율": round(blocked / n_out, 4) if n_out else 0,
							"평균 근거 문서 수": round(statistics.mean(doc_counts), 2) if doc_counts else 0,
							"평균 컨텍스트 글자 수": round(statistics.mean(ctx_chars)) if ctx_chars else 0,
						})
		print(f"  CHUNK {size}/{overlap}: 텍스트 청크 {text_count}개 · {time.perf_counter() - started:.1f}초")
	return rows


def recommend(rows: list[dict], n_in: int) -> list[dict]:
	"""최적 설정 규칙 1)~4) 순서로 거른 뒤 정렬한 목록 (맨 앞이 추천)."""
	ok = [r for r in rows if r["근거 없음 오판율"] <= MAX_MISS_RATE + 1e-9]
	if not ok:
		return []
	best_hit = max(r["근거 쪽 적중"] for r in ok)  # 문항 수로 비교 (비율 반올림 오차를 피함)
	near = [r for r in ok if r["근거 쪽 적중"] >= best_hit - HIT_TOLERANCE_QUESTIONS]
	# 같은 값이면 CSV 순서상 앞의 설정 (엑셀 시트의 MATCH와 같은 결과가 나오게 정렬은 안정 정렬만 씀)
	return sorted(near, key=lambda r: (-r["밖 질문 차단율"], r["평균 컨텍스트 글자 수"]))


# =====================================================================
# 7. 2단계: 답변까지 실행
# =====================================================================
def run_answers(args, data, id_of, report_data, emb_cache, settings, specs: list[str]) -> tuple[list[dict], list[dict]]:
	from hri.vector_store.embedding import embed_texts

	import tests.eval_qa_accuracy as qa
	from tests.eval_qa_accuracy import format_source_pages, keyword_score, run_question, source_match

	code_of = {v: k for k, v in id_of.items()}
	detail, summary = [], []
	for spec in specs:
		size, overlap, k, mmr, threshold = parse_config(spec)
		name = config_name(size, overlap, k, mmr, threshold)
		chunks = build_chunks(report_data, size, overlap)
		index = MemoryIndex(chunks, emb_cache.get([c.text for c in chunks], embed_texts))
		print(f"[2단계] {name} 답변 {len(data['items'])}문항")
		replacements = dict(
			load_settings=lambda t=threshold: settings.model_copy(update={"threshold": t}),
			vector_search=index.vector_search,
			keyword_find=index.keyword_find,
		)
		rows = []
		with patched_search(**replacements):
			for it in data["items"]:
				rids = [id_of[c] for c in it["reports"]]
				try:
					result = run_question(it["question"], rids, args.persona, k, mmr)
					acc, _ = keyword_score(result["answer"], it["keywords"], result["route"])
					src = source_match(result["sources"], it["pages"], code_of) if result["route"] == "rag" else 0
				except Exception as error:  # 한 문항이 실패해도 계속
					result = {"route": "error", "answer": f"{type(error).__name__}: {error}", "sources": [], "ms": None, "tokens": None, "cost": None}
					acc, src = None, None
				src_pages = format_source_pages(result["sources"], code_of)
				rows.append({"설정": name, "번호": it["id"], "질문": it["question"], "route": result["route"],
					"응답시간(ms)": result["ms"], "토큰": result["tokens"], "비용(원)": result["cost"],
					"정확도(자동 후보)": acc, "출처 일치": src, "응답 출처 쪽": src_pages,
					"답변": result["answer"],
					# 리포트에서 eval_qa_accuracy.item_markdown으로 그리기 위한 같은 모양의 행 (CSV에는 안 들어감)
					"_qa": {qa.C_ID: it["id"], qa.C_REPORTS: "+".join(it["reports"]), qa.C_TYPE: it["type"],
						qa.C_QUESTION: it["question"], qa.C_KEY: it["answer"], qa.C_KW: ", ".join(it["keywords"]),
						qa.C_PAGES: qa.pages_text(it["pages"]), qa.C_ROUTE: result["route"], qa.C_SCORE: None,
						qa.C_ANSWER: result["answer"], qa.C_SRC: src_pages, qa.C_MS: result["ms"], qa.C_TOKENS: result["tokens"],
						qa.C_COST: result["cost"], qa.C_ACC: acc, qa.C_SRC_OK: src,
						qa.C_REASON: qa.guess_reason(result["route"], acc or 0, src or 0) if acc is not None else "기타",
						qa.C_MEMO: f"설정 {name}"}})
		detail.extend(rows)
		ms = [r["응답시간(ms)"] for r in rows if r["응답시간(ms)"] is not None]
		costs = [r["비용(원)"] for r in rows if r["비용(원)"] is not None]
		accs = [r["정확도(자동 후보)"] for r in rows if r["정확도(자동 후보)"] is not None]
		srcs = [r["출처 일치"] for r in rows if r["출처 일치"] is not None]
		summary.append({"설정": name, "CHUNK_SIZE": size, "CHUNK_OVERLAP": overlap, "k": k, "MMR": "on" if mmr else "off",
			"THRESHOLD": threshold, "문항 수": len(rows),
			"정확도(자동 후보)": round(statistics.mean(accs), 4) if accs else None,
			"출처 일치율": round(statistics.mean(srcs), 4) if srcs else None,
			"근거 없음": sum(r["route"] == "no_answer" for r in rows),
			"평균 응답시간(ms)": round(statistics.mean(ms)) if ms else None,
			"P95 응답시간(ms)": round(float(np.percentile(ms, 95))) if ms else None,
			"문항당 비용(원)": round(statistics.mean(costs), 2) if costs else None,
			"총 비용(원)": round(sum(costs), 2) if costs else None})
	return detail, summary


def pick_final(summary: list[dict]) -> dict | None:
	scored = [s for s in summary if s["정확도(자동 후보)"] is not None]
	if not scored:
		return None
	best = max(s["정확도(자동 후보)"] for s in scored)
	near = [s for s in scored if s["정확도(자동 후보)"] >= best - ACCURACY_TOLERANCE]
	return min(near, key=lambda s: (s["문항당 비용(원)"] if s["문항당 비용(원)"] is not None else float("inf"), -s["정확도(자동 후보)"]))


# =====================================================================
# 8. 결과 저장
# =====================================================================
def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	with path.open("w", encoding="utf-8-sig", newline="") as f:
		writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
		writer.writeheader()
		writer.writerows(rows)


def _pct(x) -> str:
	return f"{x:.1%}" if isinstance(x, (int, float)) else "-"


def grid_markdown(rows: list[dict], ranked: list[dict], base: dict | None, report_data: dict, settings, conditions: str) -> list[str]:
	cols = ["설정", "근거 쪽 적중률", "1순위 적중률", "근거 없음 오판율", "밖 질문 차단율", "평균 근거 문서 수", "평균 컨텍스트 글자 수"]

	def line(r):
		return "| " + " | ".join(_pct(r[c]) if c.endswith(("율", "률")) else str(r[c]) for c in cols) + " |"

	head = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
	db_text = sum(report_data["db_text_counts"].values())
	same = [r for r in rows if r["CHUNK_SIZE"] == settings.chunk_size and r["CHUNK_OVERLAP"] == settings.chunk_overlap]
	lines = ["# 검색 설정 비교 (1단계: 검색만)", "", conditions, "",
		f"현재 설정 재현 확인: DB 텍스트 청크 {db_text}개 / 다시 나눈 청크 {same[0]['텍스트 청크 수'] if same else '-'}개 "
		"(같아야 업로드 때와 같은 방식으로 나눈 것)", "",
		"## 추천 설정 (규칙 1~4)", ""]
	if ranked:
		lines += head + [line(r) for r in ranked[:5]]
		lines += ["", f"→ **추천: {ranked[0]['설정']}** (CHUNK_SIZE/CHUNK_OVERLAP/k/MMR/THRESHOLD)"]
	else:
		lines += [f"근거 없음 오판율 {MAX_MISS_RATE:.0%} 이하인 설정이 없음 — THRESHOLD 범위를 낮춰 다시 실행"]
	if base:
		lines += ["", "## 현재 설정 (.env)", ""] + head + [line(base)]

	# 현재 설정에서 하나씩만 바꿨을 때
	if base:
		def vary(field, label):
			sel = [r for r in rows if all(r[f] == base[f] for f in ("CHUNK_SIZE", "CHUNK_OVERLAP", "k", "MMR", "THRESHOLD") if f != field)]
			if field == "CHUNK_SIZE":
				sel = [r for r in rows if r["k"] == base["k"] and r["MMR"] == base["MMR"] and r["THRESHOLD"] == base["THRESHOLD"]]
			return ["", f"## 현재 설정에서 {label}만 바꿨을 때", ""] + head + [line(r) for r in sorted(sel, key=lambda r: (r["CHUNK_SIZE"], r["CHUNK_OVERLAP"], r["k"], r["MMR"], r["THRESHOLD"]))]
		lines += vary("k", "k") + vary("MMR", "MMR") + vary("THRESHOLD", "THRESHOLD") + vary("CHUNK_SIZE", "CHUNK_SIZE·CHUNK_OVERLAP")
	return lines


# =====================================================================
# 8-1. 마크다운 리포트 (설정 고르는 계산 과정 + 질문별 기대·실제·근거)
# =====================================================================
METRIC_RULES = [
	"- **근거 쪽 적중률** = 근거 문서 중 정답 쪽을 포함하는 문서가 있는 안 질문 수 / 안 질문 수 "
	"(정답 보고서가 여럿이면 보고서마다 있어야 적중, eval_qa_accuracy의 출처 일치와 같은 기준)",
	"- **1순위 적중률** = 첫 번째 근거 문서가 정답 쪽을 포함하는 안 질문 수 / 안 질문 수",
	"- **근거 없음 오판율** = 검색 결과가 0개(THRESHOLD에 모두 걸림)인 안 질문 수 / 안 질문 수 — 낮을수록 좋음",
	"- **밖 질문 차단율** = 검색 결과가 0개인 밖 질문 수 / 밖 질문 수 — 높을수록 좋음",
	"- **평균 근거 문서 수·컨텍스트 글자 수** = get_context_docs() 규칙으로 만든 근거 문서(같은 섹션·표는 1번)의 개수·글자 수 평균 "
	"(근거를 찾은 안 질문만)",
]


def recommend_steps(rows: list[dict], n_in: int) -> list[str]:
	"""recommend()가 규칙 1~4로 후보를 줄여 간 과정을 숫자로 적는다."""
	lines = [f"전체 조합 {len(rows):,}개에서 시작한다.", ""]
	ok = [r for r in rows if r["근거 없음 오판율"] <= MAX_MISS_RATE + 1e-9]
	max_miss = int(MAX_MISS_RATE * n_in + 1e-9)
	lines.append(f"1. 근거 없음 오판율 ≤ {MAX_MISS_RATE:.0%} (안 질문 {n_in}개 중 오판 {max_miss}개 이하) → **{len(ok):,}개** 남음")
	if not ok:
		return lines + ["", "남는 설정이 없어 추천하지 않음 (THRESHOLD 범위를 낮춰 다시 실행)", ""]
	best_hit = max(r["근거 쪽 적중"] for r in ok)
	near = [r for r in ok if r["근거 쪽 적중"] >= best_hit - HIT_TOLERANCE_QUESTIONS]
	lines.append(f"2. 그중 근거 쪽 적중 최고 = {best_hit}/{n_in} → {best_hit} - {HIT_TOLERANCE_QUESTIONS} = "
		f"{best_hit - HIT_TOLERANCE_QUESTIONS}문항 이상 → **{len(near):,}개** 남음")
	best_block = max(r["밖 질문 차단율"] for r in near)
	top_block = [r for r in near if r["밖 질문 차단율"] == best_block]
	lines.append(f"3. 그중 밖 질문 차단율 최고 = {best_block:.1%} → **{len(top_block):,}개**")
	min_ctx = min(r["평균 컨텍스트 글자 수"] for r in top_block)
	final = [r for r in top_block if r["평균 컨텍스트 글자 수"] == min_ctx]
	lines.append(f"4. 그중 평균 컨텍스트 글자 수 최소 = {min_ctx:,}자 → **{len(final):,}개** "
		f"(여럿이면 CSV 순서상 앞의 것) → 추천 **{final[0]['설정']}**")
	return lines + [""]


def _docs_text(docs: list[tuple], code_of: dict[int, str]) -> str:
	return ", ".join(f"{code_of.get(d[0], d[0])} p.{d[1]}-{d[2]}" for d in docs) or "(없음)"


def question_details(spec: str, data, oos, id_of, report_data, emb_cache, expansions, settings) -> list[dict]:
	"""설정 1개로 질문 46개를 다시 검색해 질문마다 기대·실제 근거 문서·판정 근거를 만든다 (리포트용).

	1단계 run_grid와 같은 메모리 검색을 쓰되, 미리 계산해 둔 후보 대신 MemoryIndex를 바로 부른다 (결과는 같음).
	"""
	from hri.vector_store.embedding import embed_texts

	from tests.eval_qa_accuracy import explain_source_match

	size, overlap, k, mmr, threshold = parse_config(spec)
	code_of = {v: c for c, v in id_of.items()}
	sections = {(s.report_id, s.section_no): s for secs in report_data["sections"].values() for s in secs}
	chunks = build_chunks(report_data, size, overlap)
	index = MemoryIndex(chunks, emb_cache.get([c.text for c in chunks], embed_texts))
	questions = [(it, True) for it in data["items"]] + [(it, False) for it in oos["items"]]
	texts = list(dict.fromkeys(t for it, _ in questions for t in [it["question"], *expansions[it["question"]]]))
	vecs = dict(zip(texts, emb_cache.get(texts, embed_texts)))
	replacements = dict(
		load_settings=lambda t=threshold: SimpleNamespace(threshold=t),
		vector_search=index.vector_search,
		keyword_find=index.keyword_find,
		expand_queries=lambda q: expansions[q],
		embed_texts=lambda ts: [vecs[t] for t in ts],
	)
	out = []
	with patched_search(**replacements) as s:
		for it, inside in questions:
			rids = [id_of[c] for c in it["reports"]]
			found = s.hybrid_search(it["question"], k=k, report_ids=rids, use_mmr=mmr)
			docs = context_docs(found, sections, report_data["table_lengths"], settings.max_context_chars) if found else []
			row = {"번호": it["id"], "구분": "안" if inside else "밖", "질문": it["question"],
				"근거 문서": _docs_text(docs, code_of), "근거 글자 수": sum(d[3] for d in docs)}
			if inside:
				expected = " / ".join(f"{c} {', '.join(map(str, p))}쪽" for c, p in it["pages"].items())
				if not found:
					row.update({"기대": f"근거 찾음 · 정답 쪽 {expected}", "적중": FAIL, "1순위": FAIL,
						"계산": "검색 결과 0개 (모든 검색 문장의 score가 THRESHOLD 미만) → 근거 없음 오판"})
				else:
					_, _, calc = explain_source_match(it["pages"], [(code_of[d[0]], d[1], d[2]) for d in docs], "rag")
					first = first_hit(docs, it["pages"], id_of)
					row.update({"기대": f"근거 찾음 · 정답 쪽 {expected}", "적중": mark(gold_hit(docs, it["pages"], id_of)),
						"1순위": mark(first), "계산": calc.replace("**", "") + f" · 1순위 {_docs_text(docs[:1], code_of)} → {'포함' if first else '미포함'}"})
			else:
				row.update({"기대": "근거 없음 (검색 결과 0개)", "적중": mark(not found), "1순위": "-",
					"계산": "검색 결과 0개 → 차단" if not found else f"근거 문서 {len(docs)}개가 THRESHOLD를 넘음 → 차단 실패 (LLM 거절 규칙에 맡겨짐)"})
			out.append(row)
	return out


def details_markdown(title: str, spec: str, grid_row: dict | None, rows: list[dict]) -> list[str]:
	ins = [r for r in rows if r["구분"] == "안"]
	outs = [r for r in rows if r["구분"] == "밖"]
	hit = sum(r["적중"] == PASS for r in ins)
	first = sum(r["1순위"] == PASS for r in ins)
	miss = sum("근거 없음 오판" in r["계산"] for r in ins)
	block = sum(r["적중"] == PASS for r in outs)
	lines = [f"## {title}: {spec}", "", "지표 계산:", "",
		f"- 근거 쪽 적중률 = {hit} / {len(ins)} = **{hit / len(ins):.1%}**" if ins else "",
		f"- 1순위 적중률 = {first} / {len(ins)} = **{first / len(ins):.1%}**" if ins else "",
		f"- 근거 없음 오판율 = {miss} / {len(ins)} = **{miss / len(ins):.1%}**" if ins else "",
		f"- 밖 질문 차단율 = {block} / {len(outs)} = **{block / len(outs):.1%}**" if outs else ""]
	if grid_row:
		same = (grid_row["근거 쪽 적중"] == hit and grid_row["근거 없음 오판"] == miss and grid_row["밖 질문 차단"] == block)
		lines.append(f"- 1단계 표 값과 대조: {mark(same)} {'같음' if same else '다름 — 검색을 다시 돌린 결과가 표와 달라 확인 필요'}")
	headers = ["번호", "질문", "기대", "실제 근거 문서", "결과", "1순위", "계산"]
	lines += ["", "### 보고서 안 질문", ""] + table(headers, [[r["번호"], r["질문"], r["기대"], r["근거 문서"], r["적중"], r["1순위"], r["계산"]] for r in ins])
	lines += ["### 보고서 밖 질문", ""] + table(headers, [[r["번호"], r["질문"], r["기대"], r["근거 문서"], r["적중"], r["1순위"], r["계산"]] for r in outs])
	return lines


def answers_report(detail: list[dict], summary: list[dict], final: dict | None, conditions: str) -> list[str]:
	"""2단계 리포트: 설정별 표 → 최종 추천 계산 → 설정별·문항별 상세(질문·기대 답변·실제 답변·채점 근거)."""
	from tests.eval_qa_accuracy import item_markdown

	lines = ["# 검색 설정 비교 리포트 (2단계: 답변까지)", "", conditions, "",
		"## 0. 평가 방식", "",
		"1단계 상위 설정과 현재 설정으로 보고서 안 질문을 실제로 답하게 하고, eval_qa_accuracy와 같은 규칙으로 채점한다.", "",
		"- 정확도(자동 후보): 채점 키워드가 모두 있으면 1, 일부 0.5, 없으면 0 → 설정별 평균",
		"- 출처 일치율: 정답 보고서마다 응답 출처가 정답 쪽을 포함하면 1 → 설정별 평균",
		f"- 최종 추천: 정확도 최고값에서 {ACCURACY_TOLERANCE} 이내인 설정 중 문항당 비용이 가장 낮은 설정", "",
		"## 1. 설정별 결과", ""]
	lines += table(ANSWER_SUMMARY_COLUMNS, [[s[c] for c in ANSWER_SUMMARY_COLUMNS] for s in summary])
	lines += ["## 2. 최종 추천 계산 과정", ""]
	scored = [s for s in summary if s["정확도(자동 후보)"] is not None]
	if scored:
		best = max(s["정확도(자동 후보)"] for s in scored)
		near = [s for s in scored if s["정확도(자동 후보)"] >= best - ACCURACY_TOLERANCE]
		for s in scored:
			accs = [r["정확도(자동 후보)"] for r in detail if r["설정"] == s["설정"] and r["정확도(자동 후보)"] is not None]
			lines.append(f"- {s['설정']}: 정확도 = {mean_formula(accs)} · 문항당 비용 {s['문항당 비용(원)']}원")
		lines += ["", f"1. 정확도 최고 = {best:.4f}",
			f"2. {best:.4f} - {ACCURACY_TOLERANCE} = {best - ACCURACY_TOLERANCE:.4f} 이상인 설정: {', '.join(s['설정'] for s in near)}",
			f"3. 그중 문항당 비용 최소 → **{final['설정'] if final else '-'}**", "",
			"정확도는 키워드 자동 후보라 상위 설정은 아래 답변을 사람이 확인할 것.", ""]
	else:
		lines += ["채점된 설정이 없음", ""]
	for s in summary:
		lines += [f"## 3. 상세 — {s['설정']}", ""]
		for r in (r for r in detail if r["설정"] == s["설정"]):
			lines += item_markdown(r["_qa"])
	return lines


def main() -> None:
	parser = argparse.ArgumentParser(description="검색 설정(MMR·k·CHUNK_SIZE·CHUNK_OVERLAP·THRESHOLD) 비교")
	parser.add_argument("--answers", type=Path, default=DEFAULT_ANSWERS)
	parser.add_argument("--out-of-scope", type=Path, default=DEFAULT_OUT_OF_SCOPE)
	parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
	parser.add_argument("--report-ids", help="report_id 직접 지정. 예) FX=3,PR=4,GR=5")
	parser.add_argument("--sizes", default="300,500,800,1000")
	parser.add_argument("--overlaps", default="0,50,100")
	parser.add_argument("--ks", default="1-10")
	parser.add_argument("--mmr", choices=["both", "on", "off"], default="both")
	parser.add_argument("--thresholds", default="0.10:0.60:0.05")
	parser.add_argument("--answer-top", type=int, default=0, help="1단계 추천 상위 N개 + 현재 설정을 답변까지 실행")
	parser.add_argument("--answer", nargs="*", default=[], help="답변까지 실행할 설정. 예) 500/50/4/off/0.30")
	parser.add_argument("--skip-grid", action="store_true", help="1단계를 건너뛰고 --answer 설정만 실행")
	parser.add_argument("--persona", default="실무자")
	parser.add_argument("--dry-run", action="store_true", help="report_id·청크 수만 확인 (API 호출 없음)")
	args = parser.parse_args()
	args.sizes = parse_range(args.sizes)
	args.overlaps = parse_range(args.overlaps)
	args.ks = parse_range(args.ks)
	args.thresholds = parse_thresholds(args.thresholds)

	from hri.config.settings import load_settings
	from hri.retriever.search import expand_queries

	from tests.eval_qa_accuracy import load_answers, resolve_report_ids

	settings = load_settings()
	data = load_answers(args.answers)
	oos = load_answers(args.out_of_scope)
	id_of = resolve_report_ids(data, args.report_ids)
	report_data = load_report_data(list(id_of.values()))
	base_name = config_name(settings.chunk_size, settings.chunk_overlap, 4, False, settings.threshold)

	print(f"report_id: {id_of} · 현재 설정 {base_name} (k=4·MMR off는 화면 기본값)")
	for code, rid in id_of.items():
		print(f"  {code}: 섹션 {len(report_data['sections'][rid])}개 · DB 텍스트 청크 {report_data['db_text_counts'].get(rid, 0)}개")
	if args.dry_run:
		for size in args.sizes:
			for overlap in (o for o in args.overlaps if o < size):
				chunks = build_chunks(report_data, size, overlap)
				print(f"  CHUNK {size}/{overlap}: 텍스트 청크 {sum(c.chunk_type == 'text' for c in chunks)}개")
		return

	stamp = datetime.now().strftime("%Y%m%d_%H%M")
	# 캐시는 모델별로 따로 둔다 (임베딩·LLM 모델이 바뀌면 예전 벡터·확장 질문을 쓰지 않게)
	from hri.vector_store.embedding import EMBEDDING_MODEL

	emb_cache = EmbeddingCache(args.out_dir / f"cache_embeddings_{EMBEDDING_MODEL}.npz")
	conditions = (f"실행일 {datetime.now():%Y-%m-%d %H:%M} · 모델 {settings.llm_model} · 보고서 {id_of} · "
		f"안 질문 {len(data['items'])}개 · 밖 질문 {len(oos['items'])}개 · MAX_CONTEXT_CHARS={settings.max_context_chars}")

	specs = list(args.answer)
	if not args.skip_grid:
		questions = [it["question"] for it in data["items"]] + [it["question"] for it in oos["items"]]
		expansions = load_expansions(args.out_dir / f"cache_expansions_{settings.llm_model}.json", questions, expand_queries)
		rows = run_grid(args, data, oos, id_of, report_data, emb_cache, expansions, settings)
		grid_path = args.out_dir / f"search_grid_{stamp}.csv"
		write_csv(grid_path, GRID_COLUMNS, rows)
		ranked = recommend(rows, len(data["items"]))
		base = next((r for r in rows if r["설정"] == base_name), None)
		md = grid_markdown(rows, ranked, base, report_data, settings, conditions)
		# 리포트: 평가 방식 → 추천 고르는 과정 → 기존 비교표 → 추천·현재 설정의 질문별 상세
		report = [md[0], "", conditions, "", "## 0. 평가 방식", "",
			f"보고서 안 질문 {len(data['items'])}개와 밖 질문 {len(oos['items'])}개를 설정마다 hybrid_search()로 검색만 한다 (답변 LLM 없음).",
			*METRIC_RULES, "", "## 1. 추천 설정을 고른 과정", "", *recommend_steps(rows, len(data["items"]))]
		report += ["## 2. 비교표", ""] + [("#" + ln if ln.startswith("## ") else ln) for ln in md[3:]]
		detail_specs = [("3. 추천 설정 질문별 결과", ranked[0]["설정"])] if ranked else []
		if base and (not ranked or base["설정"] != ranked[0]["설정"]):
			detail_specs.append(("4. 현재 설정 질문별 결과", base["설정"]))
		for title, spec in detail_specs:
			grid_row = next((r for r in rows if r["설정"] == spec), None)
			report += [""] + details_markdown(title, spec, grid_row,
				question_details(spec, data, oos, id_of, report_data, emb_cache, expansions, settings))
		report_path = write_report(args.out_dir / f"search_grid_{stamp}_리포트.md", report)
		print(f"저장: {grid_path} · 리포트: {report_path}")
		if args.answer_top:
			chosen = []
			for r in ranked:
				if r["설정"] not in chosen:
					chosen.append(r["설정"])
				if len(chosen) == args.answer_top:
					break
			specs += [s for s in [*chosen, base_name] if s not in specs]

	if specs:
		detail, summary = run_answers(args, data, id_of, report_data, emb_cache, settings, specs)
		write_csv(args.out_dir / f"search_answers_{stamp}.csv", ANSWER_COLUMNS, detail)
		write_csv(args.out_dir / f"search_answers_{stamp}_설정별.csv", ANSWER_SUMMARY_COLUMNS, summary)
		final = pick_final(summary)
		report_path = write_report(args.out_dir / f"search_answers_{stamp}_리포트.md", answers_report(detail, summary, final, conditions))
		if final:
			print(f"최종 추천: {final['설정']} (정확도 최고값에서 {ACCURACY_TOLERANCE} 이내 중 문항당 비용 최소)")
		print(f"저장: {args.out_dir / f'search_answers_{stamp}_설정별.csv'} · 리포트: {report_path}")


if __name__ == "__main__":
	main()