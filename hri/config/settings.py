"""환경 설정 로드 · load_settings (임세준)"""

import os

from dotenv import load_dotenv

from hri.domain.schemas import Settings


def load_settings() -> Settings:
	"""
	.env를 읽어서 Settings 만들기

	1) load_dotenv()로 .env 파일 내용을 환경변수로 등록한다 (os.getenv보다 먼저 해야 함).
	2) MySQL 필수값(MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE)이 없으면 ValueError.
	   Chroma를 서버(http) 모드로 쓸 때는 CHROMA_HOST도 없으면 ValueError.
	3) MAX_CONTEXT_CHARS가 비어 있으면 None으로 바꾼다.
	4) os.getenv로 값을 꺼내 Settings를 만들어 반환한다 (기본값은 Settings 스키마와 같음).
	"""
	# 1) .env 파일 내용을 환경변수로 등록 (os.getenv보다 먼저!)
	load_dotenv()

	# 2) 꼭 있어야 하는 값이 있는지 확인
	need_names = ["MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE"]
	for name in need_names:
		if not os.getenv(name):
			raise ValueError(f"{name}이(가) .env에 없습니다. .env.example을 참고하세요.")

	# Chroma 서버에 연결할 때는 CHROMA_HOST도 있어야 함
	chroma_mode = os.getenv("CHROMA_MODE", "http")
	if chroma_mode == "http":
		if not os.getenv("CHROMA_HOST"):
			raise ValueError("CHROMA_HOST이(가) .env에 없습니다. .env.example을 참고하세요.")

	# 3) 값이 비어 있을 수 있는 것 처리
	max_context_chars = os.getenv("MAX_CONTEXT_CHARS")
	if max_context_chars == "":
		max_context_chars = None

	# 4) Settings 만들기
	settings = Settings(
		openai_api_key=os.getenv("OPENAI_API_KEY", ""),
		ecos_api_key=os.getenv("ECOS_API_KEY", ""),
		koreaexim_api_key=os.getenv("KOREAEXIM_API_KEY", ""),
		mysql_host=os.getenv("MYSQL_HOST"),
		mysql_port=os.getenv("MYSQL_PORT", "3306"),
		mysql_user=os.getenv("MYSQL_USER"),
		mysql_password=os.getenv("MYSQL_PASSWORD"),
		mysql_database=os.getenv("MYSQL_DATABASE"),
		chroma_mode=chroma_mode,
		chroma_host=os.getenv("CHROMA_HOST", "localhost"),
		chroma_port=os.getenv("CHROMA_PORT", "8000"),
		chroma_persist_path=os.getenv("CHROMA_PERSIST_PATH", "chroma_db/"),
		chroma_collection=os.getenv("CHROMA_COLLECTION", "hri_reports"),
		chroma_user=os.getenv("CHROMA_USER"),
		chroma_password=os.getenv("CHROMA_PASSWORD"),
		llm_model=os.getenv("LLM_MODEL", "gpt-6-luna"),
		vision_model=os.getenv("VISION_MODEL", "gpt-6-luna"),
		model_prices=os.getenv("MODEL_PRICES", '{"gpt-6-luna": {"input": 0.1, "output": 0.5}}'),
		ocr_langs=os.getenv("OCR_LANGS", "ko,en"),
		ocr_min_conf=os.getenv("OCR_MIN_CONF", "0.4"),
		chart_dpi=os.getenv("CHART_DPI", "200"),
		min_chart_pt=os.getenv("MIN_CHART_PT", "120"),
		graph_recursion_limit=os.getenv("GRAPH_RECURSION_LIMIT", "8"),
		max_mb=os.getenv("MAX_MB", "30"),
		max_pages=os.getenv("MAX_PAGES", "150"),
		chunk_size=os.getenv("CHUNK_SIZE", "500"),
		chunk_overlap=os.getenv("CHUNK_OVERLAP", "50"),
		threshold=os.getenv("THRESHOLD", "0.3"),
		max_context_chars=max_context_chars
	)

	return settings