"""validate_pdf()가 명세대로 동작하는지 확인하는 테스트 (네트워크·DB·.env 없이 실행).

테스트용 PDF는 pymupdf로 빈 쪽만 넣어서 tmp_path(pytest가 주는 임시 폴더)에 만든다.
"""

# pytest: 테스트를 실행하고, tmp_path·monkeypatch 같은 도구를 준다
import os

import pymupdf
import pytest

from hri.domain.schemas import Settings, ValidationResult
from hri.service import document_service
from hri.service.document_service import validate_pdf


# load_settings()는 .env의 MySQL 값이 없으면 오류를 내므로, 테스트에서는 가짜 설정으로 바꿔 끼운다
def fake_settings(max_mb=30, max_pages=150):
	return Settings(
		mysql_host="localhost",
		mysql_user="test",
		mysql_password="test",
		mysql_database="test",
		max_mb=max_mb,
		max_pages=max_pages,
	)


@pytest.fixture(autouse=True)
def use_fake_settings(monkeypatch):
	monkeypatch.setattr(document_service, "load_settings", lambda: fake_settings())


# 빈 쪽 page_count개짜리 PDF를 만들어 경로를 돌려준다 (password를 주면 여는 암호를 건다)
def make_pdf(path, page_count=1, password=None):
	doc = pymupdf.open()
	for _ in range(page_count):
		doc.new_page(width=595, height=842)
	if password is None:
		doc.save(str(path))
	else:
		doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password)
	doc.close()
	return str(path)


# [확인 1] 정상 PDF는 ok=True, reason=None
def test_valid_pdf(tmp_path):
	result = validate_pdf(make_pdf(tmp_path / "ok.pdf", page_count=3))
	assert isinstance(result, ValidationResult)
	assert result.ok is True and result.reason is None


# [확인 2] 확장자가 .pdf가 아니면 실패 (명세 예시 문구 그대로)
def test_not_pdf_extension(tmp_path):
	path = tmp_path / "report.txt"
	path.write_text("hello")
	result = validate_pdf(str(path))
	assert result.ok is False
	assert result.reason == "PDF 파일만 업로드할 수 있습니다"


# [확인 3] 확장자가 대문자(.PDF)여도 통과
def test_upper_case_extension(tmp_path):
	assert validate_pdf(make_pdf(tmp_path / "OK.PDF")).ok is True


# [확인 4] 파일이 없으면 예외 없이 실패
def test_missing_file(tmp_path):
	result = validate_pdf(str(tmp_path / "none.pdf"))
	assert result.ok is False and "찾을 수 없습니다" in result.reason


# [확인 5] 용량이 MAX_MB를 넘으면 실패 (MAX_MB=1로 바꾸고 2MB 파일로 확인)
def test_too_large(tmp_path, monkeypatch):
	monkeypatch.setattr(document_service, "load_settings", lambda: fake_settings(max_mb=1))
	path = tmp_path / "big.pdf"
	path.write_bytes(b"0" * (2 * 1024 * 1024))
	result = validate_pdf(str(path))
	assert result.ok is False and "1MB" in result.reason


# [확인 6] pymupdf로 열리지 않는 파일(PDF가 아닌 내용)은 실패
def test_broken_pdf(tmp_path):
	path = tmp_path / "broken.pdf"
	path.write_bytes(b"this is not a pdf")
	result = validate_pdf(str(path))
	assert result.ok is False and "열 수 없습니다" in result.reason


# [확인 7] 암호가 걸린 PDF는 실패
def test_encrypted_pdf(tmp_path):
	result = validate_pdf(make_pdf(tmp_path / "lock.pdf", password="1234"))
	assert result.ok is False and "암호" in result.reason


# [확인 8] 쪽 수가 MAX_PAGES를 넘으면 실패, 딱 MAX_PAGES면 통과 (MAX_PAGES=2로 확인)
def test_too_many_pages(tmp_path, monkeypatch):
	monkeypatch.setattr(document_service, "load_settings", lambda: fake_settings(max_pages=2))
	assert validate_pdf(make_pdf(tmp_path / "two.pdf", page_count=2)).ok is True
	result = validate_pdf(make_pdf(tmp_path / "three.pdf", page_count=3))
	assert result.ok is False and "2쪽" in result.reason


# [확인 9] 이름만 .pdf인 이미지(png)는 pymupdf가 열어도 PDF가 아니므로 실패
def test_image_renamed_to_pdf(tmp_path):
	path = tmp_path / "image.pdf"
	pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10), False)
	path.write_bytes(pix.tobytes("png"))
	result = validate_pdf(str(path))
	assert result.ok is False
	assert result.reason == "PDF 파일만 업로드할 수 있습니다"


# [확인 10] 빈 파일(0바이트)은 예외 없이 실패
def test_empty_file(tmp_path):
	path = tmp_path / "empty.pdf"
	path.write_bytes(b"")
	result = validate_pdf(str(path))
	assert result.ok is False and "열 수 없습니다" in result.reason


# [확인 11] 검사가 끝나면 파일이 닫혀 있어야 한다 (Windows는 열린 파일을 못 지우므로, 지워지면 닫힌 것)
def test_file_closed_after_check(tmp_path):
	path = make_pdf(tmp_path / "close.pdf")
	validate_pdf(path)
	os.remove(path)
	assert not os.path.exists(path)


# [확인 12] 쪽이 0개인 PDF는 실패 (pymupdf는 0쪽 저장이 안 돼서 PDF 내용을 직접 씀)
def test_zero_page_pdf(tmp_path):
	path = tmp_path / "zero.pdf"
	path.write_bytes(
		b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
		b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
		b"trailer<</Root 1 0 R>>\n%%EOF"
	)
	result = validate_pdf(str(path))
	assert result.ok is False and "쪽이 하나도 없는" in result.reason
