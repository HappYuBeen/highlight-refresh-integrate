"""보고서 업로드 처리 · upload_report (김희진) / validate_pdf (엄정현)"""

# 엄정현 validate_pdf() 추가.
import os
import logging
import pymupdf
from collections.abc import Iterator
from langchain_core.callbacks import UsageMetadataCallbackHandler
from hri.graph.registry import get_ingest_graph
from hri.llm.llm_client import cost_korean_won, get_usd_krw
from hri.dao.usage_dao import insert_usage
from hri.dao.report_dao import make_file_hash, select_report_by_hash
from hri.config.settings import load_settings
from hri.domain.schemas import LlmUsage, ValidationResult
from hri.domain.schemas import LlmUsage

logger = logging.getLogger(__name__)

def validate_pdf(file_path: str) -> ValidationResult:
	"""업로드된 파일이 PDF로 처리할 수 있는지 확인한다.

	확인 순서: 확장자(.pdf) → 용량(MAX_MB) → pymupdf로 열리는지 → 진짜 PDF인지 → 암호화 여부 → 쪽 수(MAX_PAGES)
	예외를 던지지 않고 ValidationResult(ok, reason)만 돌려준다.
	실패하면 validate 노드가 reason을 state['error']에 기록하고, on_upload()가 화면에 보여 준다.
	글자가 없는 스캔본은 여기서 막지 않는다 (load_pdf()가 Page.is_scanned로 표시).

	예) validate_pdf("data/raw/report.pdf") → ValidationResult(ok=True, reason=None)
	"""
	# MAX_MB·MAX_PAGES는 .env 값 (load_settings()가 Settings.max_mb·max_pages로 읽어 둠)
	settings = load_settings()
	max_mb = settings.max_mb
	max_pages = settings.max_pages

	# 1) 확장자 확인 (.PDF처럼 대문자로 올라와도 통과시키려고 소문자로 바꿔서 비교)
	ext = os.path.splitext(file_path)[1].lower()
	if ext != ".pdf":
		return ValidationResult(ok=False, reason="PDF 파일만 업로드할 수 있습니다")

	# 파일이 없으면 os.path.getsize()가 예외를 내므로 먼저 확인
	if not os.path.isfile(file_path):
		return ValidationResult(ok=False, reason="파일을 찾을 수 없습니다. 다시 업로드해 주세요")

	# 2) 용량 확인 (바이트 → MB: 1MB = 1024 * 1024 바이트)
	size_mb = os.path.getsize(file_path) / (1024 * 1024)
	if size_mb > max_mb:
		return ValidationResult(ok=False, reason=f"파일 용량이 {max_mb}MB를 넘습니다 (현재 {size_mb:.1f}MB)")

	# 3) pymupdf로 열기 (깨진 파일·빈 파일·PDF가 아닌 내용은 여기서 오류가 난다)
	try:
		doc = pymupdf.open(file_path)
	except Exception:
		return ValidationResult(ok=False, reason="PDF 파일을 열 수 없습니다. 파일이 손상되지 않았는지 확인해 주세요")

	# with: 확인이 끝나면 파일을 꼭 닫는다 (안 닫으면 Windows에서 파일이 잠긴 채로 남음)
	with doc:
		# 이름만 .pdf인 이미지(png 등)도 pymupdf는 열어 버리므로 진짜 PDF인지 한 번 더 확인
		if not doc.is_pdf:
			return ValidationResult(ok=False, reason="PDF 파일만 업로드할 수 있습니다")

		# 4) 암호화 여부 확인 (needs_pass: 열 때 암호를 입력해야 하는 파일이면 True)
		if doc.needs_pass:
			return ValidationResult(ok=False, reason="암호가 걸린 PDF는 업로드할 수 없습니다. 암호를 해제한 뒤 다시 올려 주세요")

		# 5) 쪽 수 확인
		page_count = doc.page_count
		if page_count < 1:
			return ValidationResult(ok=False, reason="쪽이 하나도 없는 PDF입니다. 내용이 있는 파일을 올려 주세요")
		if page_count > max_pages:
			return ValidationResult(ok=False, reason=f"쪽 수가 {max_pages}쪽을 넘습니다 (현재 {page_count}쪽)")

	# 6) 모두 통과
	return ValidationResult(ok=True)

# (김희진)

def _cost_krw(model: str, tokens: dict) -> float | None:
	"""모델 1개의 원화 비용.
	환율·단가를 못 구해도(.env MODEL_PRICES가 null 등) 토큰 기록은 남기려고 None으로 둔다."""
	try:
		return cost_korean_won({model: tokens}, get_usd_krw())
	except Exception:
		logger.exception("업로드 비용 계산 실패: model=%s", model)
		return None

def _record_usage(usage: UsageMetadataCallbackHandler, user_id: str, report_id: int | None) -> None:
	"""그래프에서 쓴 AI 사용량을 모델별로 llm_usage에 저장한다 (flow='ingest').
	업로드 결과와 상관없이 쓴 비용은 남기려고 finally에서 부른다.
	모델마다 따로 처리한다: 한 모델의 비용 계산·DB 저장이 실패해도 나머지 모델 기록은 남긴다.
	여기서 나는 오류(환율 조회, DB)는 로그만 남기고 삼킨다: 이미 끝난 업로드를 오류처럼 보이게 하지 않으려고.
	"""
	if not usage.usage_metadata:
		return
	for model, tokens in usage.usage_metadata.items():
		try:
			insert_usage(LlmUsage(
				flow="ingest",
				user_id=user_id,
				ref_id=report_id,
				model=model,
				input_tokens=tokens.get("input_tokens", 0),
				output_tokens=tokens.get("output_tokens", 0),
				cost_krw=_cost_krw(model, tokens),
			))
		except Exception:
			logger.exception("업로드 사용량 기록 실패: model=%s, report_id=%s", model, report_id)

def upload_report(file_path: str, user_id: str) -> Iterator[dict]:
	"""PDF를 업로드 그래프로 처리하며 진행 상황을 하나씩 내보낸다.

	내보내는 dict 모양
	진행: {"type": "progress", "step": 노드이름}
	성공: {"type": "result", "report_id": int, "counts": dict, "warnings": list, "meta": dict | None, "page_count": int}
	실패: {"type": "error", "reason": str}
	warnings는 load_text, extract_tables, process_charts등 모든 노드가 쌓은 것을 모아서 담는다.
	meta(제목, 발행기관, 발행일)와 page_count는 load_text 노드 결과로, 업로드 결과 화면(on_upload)이 쓴다.
	"""
	graph = get_ingest_graph()
	usage = UsageMetadataCallbackHandler()
	report_id: int | None = None
	result: dict | None = None
	warnings: list[str] = []
	meta: dict | None = None
	page_count = 0
	try:
		# 같은 사람이 같은 파일을 이미 올렸다면 그래프(LLM·Vision 호출)를 돌리지 않고 안내만 한다.
		duplicate = select_report_by_hash(make_file_hash(file_path, user_id))
		if duplicate:
			yield {"type": "error", "reason": f"이미 업로드한 보고서입니다: {duplicate.title} (보고서 {duplicate.report_id})"}
			return

		for update in graph.stream(
			{"file_path": file_path, "user_id": user_id},
			stream_mode = "updates",
			config = {"callbacks": [usage]},
		):
			for node, state in update.items():
				yield {"type": "progress", "step": node}
				state = state or {}
				warnings.extend(state.get("warnings") or [])

				if node == "load_text":
					meta = state.get("meta")
					page_count = len(state.get("pages") or [])
				if node == "validate" and state.get("error"):
					result = {"type": "error", "reason": state.get("error")}
				if node == "persist" and state.get("report_id") is not None:
					report_id = state["report_id"]
					result = {"type": "result", "report_id": report_id, "counts": state.get("counts", {}), "warnings": warnings, "meta": meta, "page_count": page_count}
		yield result or {"type": "error", "reason": "저장 단계까지 진행되지 않았습니다."}

	except Exception:
		logger.exception("upload_report 실패: %s", file_path)
		yield {"type": "error", "reason": "업로드 처리 중 오류가 발생했습니다."}

	finally:
		_record_usage(usage, user_id, report_id)