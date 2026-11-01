"""4.1.7 예외 상황 점검 — 빈 입력 · 초장문 · 암호화 PDF · 대용량 PDF (서동안)

확인하는 것
	1. 질문 입력: 빈 질문·공백만·1,000자 경계(validate_question), 초장문에서도 mask_pii가 빨리 끝나는지
	2. PDF 검사(validate_pdf): 암호 PDF·용량 초과·쪽 수 초과·빈 파일·가짜 PDF·스캔본
	3. 업로드 전체(upload_report): 암호·대용량 PDF가 validate에서 멈추고 안내 문구만 나오는지
	4. 초장문 섹션: 요약할 때 원문이 MAX_SECTION_CHARS(8,000자)까지만 LLM에 가는지
	5. qa_graph의 validate 노드가 빈 질문·초장문을 막는지 (validate_question 연결 후)

테스트용 PDF는 pymupdf로 tmp_path(임시 폴더)에 바로 만들고, 끝나면 pytest가 지운다.
.env는 모듈을 불러올 때만 필요하고, DB·OpenAI·Chroma에는 접속하지 않는다 (한도·LLM은 가짜로 바꿔 넣음).

실행 (프로젝트 루트에서)
	poetry run python -m pytest tests/test_edge_cases.py -v
"""

import os
import time
from types import SimpleNamespace

import pymupdf
import pytest

from hri.exception.errors import InvalidInputError
from hri.service import document_service
from hri.service.document_service import validate_pdf
from hri.service.qa_service import MAX_QUESTION_LENGTH, mask_pii, validate_question

# 지금 코드의 안내 문구. 문구를 바꾸면 여기만 고친다
EMPTY_MESSAGE = "질문을 입력해 주세요."
TOO_LONG_MESSAGE = f"질문은 {MAX_QUESTION_LENGTH:,}자 이하로 입력해 주세요."
ENCRYPTED_MESSAGE = "암호가 걸린 PDF는 업로드할 수 없습니다. 암호를 해제한 뒤 다시 올려 주세요"

# .env 기본값과 같은 한도 (settings.py: MAX_MB=30, MAX_PAGES=150)
DEFAULT_MAX_MB = 30
DEFAULT_MAX_PAGES = 150


# ---------------------------------------------------------------------
# 공통 준비
# ---------------------------------------------------------------------
def use_limits(monkeypatch, max_mb=DEFAULT_MAX_MB, max_pages=DEFAULT_MAX_PAGES):
	"""validate_pdf가 .env 대신 이 한도를 쓰게 한다."""
	monkeypatch.setattr(document_service, "load_settings", lambda: SimpleNamespace(max_mb=max_mb, max_pages=max_pages))


@pytest.fixture(autouse=True)
def default_limits(monkeypatch):
	use_limits(monkeypatch)
	# 업로드 전체 테스트(5절)가 DB에 접속하지 않게: 같은 파일 중복 확인·사용량 기록을 가짜로 바꾼다
	monkeypatch.setattr(document_service, "select_report_by_hash", lambda file_hash: None)
	monkeypatch.setattr(document_service, "insert_usage", lambda usage: None)


def make_pdf(path, pages=1, text="2025년 하반기 경제전망 본문", **save_options):
	"""글자가 있는 PDF를 만든다. text=None이면 글자 없는 빈 쪽(스캔본과 같음)."""
	doc = pymupdf.open()
	for _ in range(pages):
		page = doc.new_page()
		if text:
			page.insert_text((72, 72), text, fontname="korea")
	doc.save(str(path), **save_options)
	doc.close()
	return str(path)


def make_big_pdf(path, size_mb):
	"""1쪽짜리 PDF에 압축되지 않는 데이터(첨부 파일)를 넣어 원하는 용량으로 만든다."""
	doc = pymupdf.open()
	doc.new_page().insert_text((72, 72), "big file")
	doc.embfile_add("padding.bin", os.urandom(int(size_mb * 1024 * 1024)))
	doc.save(str(path))
	doc.close()
	return str(path)


def file_mb(path) -> float:
	return os.path.getsize(path) / (1024 * 1024)


# ---------------------------------------------------------------------
# 1. 질문 입력 — 빈 입력 · 초장문
# ---------------------------------------------------------------------
@pytest.mark.parametrize("question", ["", " ", "     ", "\n", "\t\n  ", "　"], ids=["빈칸", "공백1", "공백여러개", "줄바꿈", "탭줄바꿈", "전각공백"])
def test_empty_question_is_rejected(question):
	with pytest.raises(InvalidInputError) as error:
		validate_question(question)
	assert str(error.value) == EMPTY_MESSAGE


def test_question_at_max_length_passes():
	question = "가" * MAX_QUESTION_LENGTH
	assert validate_question(question) == question


def test_question_over_max_length_is_rejected():
	with pytest.raises(InvalidInputError) as error:
		validate_question("가" * (MAX_QUESTION_LENGTH + 1))
	assert str(error.value) == TOO_LONG_MESSAGE


def test_very_long_question_is_rejected():
	"""붙여넣기 같은 초장문(5만 자)도 같은 문구로 막힌다."""
	with pytest.raises(InvalidInputError) as error:
		validate_question("환율 전망을 알려줘. " * 5000)
	assert str(error.value) == TOO_LONG_MESSAGE


def test_length_is_counted_after_strip():
	"""앞뒤 공백은 글자 수에 넣지 않는다 (1,000자 + 공백은 통과, 앞뒤 공백은 떼고 돌려줌)."""
	question = "가" * MAX_QUESTION_LENGTH
	assert validate_question(f"   {question}\n") == question


def test_emoji_and_korean_count_as_one_char():
	question = "📈" * MAX_QUESTION_LENGTH
	assert validate_question(question) == question


def test_mask_pii_is_fast_on_long_text():
	"""라벨이 많은 긴 글에서도 정규식이 오래 걸리지 않는다 (1,000자 질문 기준 1초 안)."""
	question = ("비밀번호: abc!@#123 아이디: user_01 이름: 홍길동 " * 40)[:MAX_QUESTION_LENGTH]
	started = time.perf_counter()
	masked = mask_pii(question)
	assert time.perf_counter() - started < 1.0
	assert "abc!@#123" not in masked and "user_01" not in masked and "홍길동" not in masked


# ---------------------------------------------------------------------
# 2. PDF 검사 — 암호화
# ---------------------------------------------------------------------
def test_normal_pdf_passes(tmp_path):
	result = validate_pdf(make_pdf(tmp_path / "report.pdf", pages=3))
	assert result.ok is True


def test_pdf_with_open_password_is_rejected(tmp_path):
	"""열 때 암호를 물어보는 PDF(user password)는 막는다."""
	path = make_pdf(tmp_path / "locked.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="1234", owner_pw="owner")
	result = validate_pdf(path)
	assert result.ok is False
	assert result.reason == ENCRYPTED_MESSAGE


@pytest.mark.parametrize("method", [pymupdf.PDF_ENCRYPT_RC4_128, pymupdf.PDF_ENCRYPT_AES_128], ids=["RC4", "AES128"])
def test_other_encryption_methods_are_rejected(tmp_path, method):
	path = make_pdf(tmp_path / "locked.pdf", encryption=method, user_pw="1234", owner_pw="owner")
	assert validate_pdf(path).reason == ENCRYPTED_MESSAGE


def test_permission_only_pdf_passes_and_text_is_readable(tmp_path):
	"""열기 암호 없이 인쇄·복사 제한만 걸린 PDF(owner password)는 통과하고, 글도 읽힌다.
	보고서 PDF에 흔한 형태라 막지 않는 것이 맞다."""
	from hri.loader.pdf_loader import load_pdf

	path = make_pdf(tmp_path / "protected.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner",
		permissions=pymupdf.PDF_PERM_PRINT)
	assert validate_pdf(path).ok is True
	assert "경제전망" in load_pdf(path)[0].text


# ---------------------------------------------------------------------
# 3. PDF 검사 — 대용량 (용량 · 쪽 수)
# ---------------------------------------------------------------------
def test_pdf_under_size_limit_passes(tmp_path, monkeypatch):
	use_limits(monkeypatch, max_mb=1)
	path = make_big_pdf(tmp_path / "small.pdf", 0.8)
	assert file_mb(path) < 1
	assert validate_pdf(path).ok is True


def test_pdf_over_size_limit_is_rejected(tmp_path, monkeypatch):
	use_limits(monkeypatch, max_mb=1)
	path = make_big_pdf(tmp_path / "big.pdf", 1.2)
	result = validate_pdf(path)
	assert result.ok is False
	assert result.reason.startswith("파일 용량이 1MB를 넘습니다")
	assert f"현재 {file_mb(path):.1f}MB" in result.reason


def test_pdf_over_default_30mb_is_rejected(tmp_path):
	"""실제 기본 한도(30MB)로 확인. 31MB 파일을 만들어서 몇 초 걸릴 수 있다."""
	path = make_big_pdf(tmp_path / "big31.pdf", 31)
	result = validate_pdf(path)
	assert result.ok is False
	assert "30MB를 넘습니다" in result.reason


def test_pdf_at_page_limit_passes(tmp_path):
	path = make_pdf(tmp_path / "p150.pdf", pages=DEFAULT_MAX_PAGES, text=None)
	assert validate_pdf(path).ok is True


def test_pdf_over_page_limit_is_rejected(tmp_path):
	path = make_pdf(tmp_path / "p151.pdf", pages=DEFAULT_MAX_PAGES + 1, text=None)
	result = validate_pdf(path)
	assert result.ok is False
	assert result.reason == f"쪽 수가 {DEFAULT_MAX_PAGES}쪽을 넘습니다 (현재 {DEFAULT_MAX_PAGES + 1}쪽)"


# ---------------------------------------------------------------------
# 4. PDF 검사 — 그 밖의 이상한 파일
# ---------------------------------------------------------------------
def test_not_pdf_extension_is_rejected(tmp_path):
	path = tmp_path / "report.txt"
	path.write_text("hello", encoding="utf-8")
	assert validate_pdf(str(path)).reason == "PDF 파일만 업로드할 수 있습니다"


def test_uppercase_extension_passes(tmp_path):
	assert validate_pdf(make_pdf(tmp_path / "REPORT.PDF")).ok is True


def test_missing_file_is_rejected(tmp_path):
	assert validate_pdf(str(tmp_path / "없는파일.pdf")).reason == "파일을 찾을 수 없습니다. 다시 업로드해 주세요"


def test_empty_file_is_rejected(tmp_path):
	path = tmp_path / "empty.pdf"
	path.write_bytes(b"")
	result = validate_pdf(str(path))
	assert result.ok is False
	assert "열 수 없습니다" in result.reason


def test_broken_pdf_is_rejected(tmp_path):
	path = tmp_path / "broken.pdf"
	path.write_bytes(b"%PDF-1.7\n" + os.urandom(2000))
	assert validate_pdf(str(path)).ok is False


def test_image_renamed_to_pdf_is_rejected(tmp_path):
	"""이름만 .pdf인 PNG 이미지는 막는다."""
	pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10), False)
	path = tmp_path / "image.pdf"
	path.write_bytes(pix.tobytes("png"))
	assert validate_pdf(str(path)).reason == "PDF 파일만 업로드할 수 있습니다"


def test_scanned_pdf_passes_and_pages_are_marked(tmp_path):
	"""글자 없는 스캔본은 막지 않고, load_pdf가 is_scanned=True로 표시만 한다."""
	from hri.loader.pdf_loader import load_pdf

	path = make_pdf(tmp_path / "scan.pdf", pages=2, text=None)
	assert validate_pdf(path).ok is True
	assert [page.is_scanned for page in load_pdf(path)] == [True, True]


# ---------------------------------------------------------------------
# 5. 업로드 전체 (upload_report) — 검사에서 멈추는지
# ---------------------------------------------------------------------
def run_upload(path):
	from hri.graph.registry import get_ingest_graph
	from hri.service.document_service import upload_report

	get_ingest_graph.cache_clear()
	events = list(upload_report(path, "tester"))
	get_ingest_graph.cache_clear()
	return events


def test_upload_encrypted_pdf_stops_at_validate(tmp_path):
	"""암호 PDF는 validate에서 끝나고, 글 추출·차트·저장 단계로 가지 않는다 (LLM·DB 호출 없음)."""
	path = make_pdf(tmp_path / "locked.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="1234", owner_pw="owner")
	events = run_upload(path)
	assert [e["step"] for e in events if e["type"] == "progress"] == ["validate"]
	assert events[-1] == {"type": "error", "reason": ENCRYPTED_MESSAGE}


def test_upload_big_pdf_stops_at_validate(tmp_path, monkeypatch):
	use_limits(monkeypatch, max_mb=1)
	events = run_upload(make_big_pdf(tmp_path / "big.pdf", 1.2))
	assert [e["step"] for e in events if e["type"] == "progress"] == ["validate"]
	assert events[-1]["type"] == "error"
	assert events[-1]["reason"].startswith("파일 용량이 1MB를 넘습니다")


def test_upload_too_many_pages_stops_at_validate(tmp_path):
	events = run_upload(make_pdf(tmp_path / "p151.pdf", pages=DEFAULT_MAX_PAGES + 1, text=None))
	assert events[-1] == {"type": "error", "reason": f"쪽 수가 {DEFAULT_MAX_PAGES}쪽을 넘습니다 (현재 {DEFAULT_MAX_PAGES + 1}쪽)"}


# ---------------------------------------------------------------------
# 6. 초장문 섹션 — 요약할 때 앞부분만 보내는지
# ---------------------------------------------------------------------
class RecordingModel:
	"""LLM 대신 쓰는 가짜 모델. 받은 메시지를 기록하고 정해진 요약을 돌려준다."""

	def __init__(self):
		self.calls = []

	def with_structured_output(self, schema):
		self.schema = schema
		return self

	def invoke(self, messages):
		self.calls.append(messages)
		return self.schema(title="x", summary="요약 문장입니다.")


def test_long_section_is_cut_before_llm(monkeypatch):
	from hri.domain.schemas import Section
	from hri.prompt import summary_prompt

	model = RecordingModel()
	monkeypatch.setattr(summary_prompt, "get_chat_model", lambda **kwargs: model)

	long_text = "가" * summary_prompt.MAX_SECTION_CHARS + "나" * 50_000  # 8,000자 뒤에 5만 자
	section = Section(section_no=0, title="1. 개요", start_page=1, end_page=40, text=long_text)
	result = summary_prompt.summarize_sections([section])

	human = model.calls[0][1][1]
	assert "나" not in human  # 8,000자 뒤 내용은 보내지 않음
	assert human.count("가") == summary_prompt.MAX_SECTION_CHARS
	assert result[0].summary == "요약 문장입니다."


# ---------------------------------------------------------------------
# 7. qa_graph의 validate 노드 (validate_question 연결 후)
# ---------------------------------------------------------------------
def _validate_node():
	from hri.graph.qa_graph import build_qa_graph

	return build_qa_graph().nodes["validate"].runnable


def test_qa_graph_validate_node_blocks_empty_question():
	result = _validate_node().invoke({"question": "   "})
	assert result.get("error") == EMPTY_MESSAGE


def test_qa_graph_validate_node_blocks_long_question():
	result = _validate_node().invoke({"question": "가" * (MAX_QUESTION_LENGTH + 1)})
	assert result.get("error") == TOO_LONG_MESSAGE