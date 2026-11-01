"""보고서 스키마(report.py)·ChartExtractError가 명세대로 동작하는지 확인하는 테스트 (네트워크·DB 없이 실행)."""

# pytest: 테스트를 실행하고, "이 코드에서 오류가 나야 한다"를 검사하는 도구(pytest.raises)를 준다
import pytest
# ValidationError: Pydantic이 값이 틀렸을 때 내는 오류의 종류
from pydantic import ValidationError

# report.py의 __all__에 넣은 이름은 hri.domain.schemas에서 바로 불러올 수 있어야 한다
from hri.domain.schemas import (
	ChartData,
	ChartImage,
	ChartSeries,
	Chunk,
	OcrToken,
	Page,
	Report,
	Section,
	Table,
	ValidationResult,
)
from hri.domain.schemas import report as report_module
from hri.exception.errors import ChartExtractError


# [확인 1] __all__에 모델 10개가 모두 들어 있어야 한다
def test_all_lists_ten_models():
	assert set(report_module.__all__) == {
		"Page", "Section", "Chunk", "Table", "Report",
		"ValidationResult", "ChartImage", "OcrToken", "ChartSeries", "ChartData",
	}


# [확인 2] 10/21 report_schema.py의 옛 이름(page_no·section_index·chunk_index)은 없어야 한다
def test_renamed_fields():
	assert "page" in Page.model_fields and "page_no" not in Page.model_fields
	assert "section_no" in Section.model_fields and "section_index" not in Section.model_fields
	assert "chunk_no" in Chunk.model_fields and "chunk_index" not in Chunk.model_fields


# [확인 3] 쪽 번호는 1부터: page=0이면 오류
def test_page_starts_from_one():
	with pytest.raises(ValidationError):
		Page(page=0, text="표지")
	assert Page(page=1).is_scanned is False


# [확인 4] Chunk.chunk_type은 text·table·chart만 받고, 기본값은 text·doc_id None
def test_chunk_type_literal():
	chunk = Chunk(chunk_no=0, section_no=0, page=3, text="물가 상승률이 둔화되었다.")
	assert chunk.chunk_type == "text" and chunk.doc_id is None
	Chunk(chunk_no=1, section_no=0, page=3, chunk_type="chart", text="<그림> 소비자물가", doc_id="abc")
	with pytest.raises(ValidationError):
		Chunk(chunk_no=2, section_no=0, page=3, chunk_type="image", text="x")


# [확인 5] Table 기본값: source_type='table', needs_review=False, doc_id는 uuid가 자동으로 채워짐
def test_table_defaults():
	t1 = Table(page=5, markdown="| 연도 | 성장률 |\n|---|---|\n| 2026 | 2.7 |")
	t2 = Table(page=5)
	assert t1.source_type == "table" and t1.needs_review is False
	assert t1.image_path is None and t1.confidence is None
	assert len(t1.doc_id) == 36 and t1.doc_id != t2.doc_id
	with pytest.raises(ValidationError):
		Table(page=5, source_type="image")


# [확인 6] Report: 발행일 글자를 날짜로 바꾸고, 발행기관을 못 찾으면 None
def test_report_defaults_and_date():
	r = Report(
		title="2026년 수정 경제 전망", published_date="2026-07-31", page_count=24,
		file_path="data/raw/a.pdf", uploaded_by="user01",
	)
	assert r.publisher is None  # 못 찾으면 None (DB NULL), 화면에서만 '확인할 수 없는 발행기관'으로 표시
	assert str(r.published_date) == "2026-07-31"
	assert r.report_id is None and r.chunk_count == 0
	assert Report(title="제목", file_path="a.pdf", uploaded_by="u").published_date is None


# [확인 7] ValidationResult: 통과면 reason None, 실패면 사유를 담는다
def test_validation_result():
	assert ValidationResult(ok=True).reason is None
	bad = ValidationResult(ok=False, reason="PDF 파일만 업로드할 수 있습니다")
	assert bad.ok is False and "PDF" in bad.reason


# [확인 8] ChartData: 못 읽은 값은 None 허용, chart_type은 정해진 값만, JSON 저장 후 복원해도 같아야 한다
def test_chart_data_roundtrip():
	data = ChartData(
		chart_type="line", title="소비자물가 상승률", unit="%",
		x_labels=["2025", "2026"],
		series=[ChartSeries(name="전년동기비", values=[2.3, None])],
	)
	assert data.needs_review is False and data.confidence is None
	assert ChartData.model_validate_json(data.model_dump_json()) == data
	with pytest.raises(ValidationError):
		ChartData(chart_type="radar", x_labels=[], series=[])


# [확인 9] OcrToken·ChartImage: 신뢰도는 0~1, 쪽은 1부터
def test_ocr_token_and_chart_image():
	OcrToken(text="2.7", bbox=[10, 20, 40, 32], conf=0.91, is_number=True)
	with pytest.raises(ValidationError):
		OcrToken(text="2.7", bbox=[10, 20, 40, 32], conf=1.5)
	with pytest.raises(ValidationError):
		ChartImage(page=0, bbox=[0, 0, 100, 100], image_path="data/charts/raw/x.png")


# [확인 10] ChartExtractError는 Exception을 상속하고, 메시지를 str()로 꺼낼 수 있어야 한다
def test_chart_extract_error():
	with pytest.raises(ChartExtractError) as info:
		raise ChartExtractError("5쪽 차트: Vision 응답을 ChartData로 바꾸지 못함")
	assert isinstance(info.value, Exception)
	assert "5쪽" in str(info.value)


# [확인 11] file_hash는 만들 때마다 36자 uuid가 자동으로 채워지고, 36자가 아니면 오류
def test_report_file_hash_uuid():
	a = Report(title="제목", file_path="a.pdf", uploaded_by="u")
	b = Report(title="제목", file_path="a.pdf", uploaded_by="u")
	assert len(a.file_hash) == 36 and a.file_hash != b.file_hash
	with pytest.raises(ValidationError):
		Report(title="제목", file_path="a.pdf", uploaded_by="u", file_hash="abc")


# [확인 12] DB 칸 길이(VARCHAR)를 넘는 값은 모델에서 막아야 한다
def test_max_length_matches_db():
	with pytest.raises(ValidationError):
		Report(title="x" * 256, file_path="a.pdf", uploaded_by="u")
	with pytest.raises(ValidationError):
		Section(section_no=0, title="x" * 256, start_page=1, end_page=1, text="본문")
	with pytest.raises(ValidationError):
		Chunk(chunk_no=0, section_no=0, page=1, text="본문", category="x" * 51)


# [확인 13] bbox는 정확히 4개만 받는다 (ChartImage·OcrToken)
def test_bbox_requires_four_values():
	assert ChartImage(page=1, bbox=[0, 0, 100, 80], image_path="x.png").bbox == (0, 0, 100, 80)
	with pytest.raises(ValidationError):
		ChartImage(page=1, bbox=[0, 0, 100], image_path="x.png")
	with pytest.raises(ValidationError):
		OcrToken(text="2.7", bbox=[10, 20, 40], conf=0.9)


# [확인 14] ChartData는 title·unit을 못 찾으면 None
def test_chart_data_title_unit_default_none():
	data = ChartData(chart_type="bar", x_labels=["2025"], series=[ChartSeries(name="한국", values=[2.1])])
	assert data.title is None and data.unit is None


# [확인 15] DB 행으로 Table을 만들면 ocr_confidence는 버려진다 → DAO에서 'ocr_confidence AS confidence'로 읽어야 함
def test_table_from_db_row_needs_alias():
	row = {"doc_id": "d1", "report_id": 1, "page": 3, "markdown": "m", "source_type": "chart", "ocr_confidence": 0.42, "needs_review": 1}
	assert Table(**row).confidence is None
	row["confidence"] = row.pop("ocr_confidence")
	t = Table(**row)
	assert t.confidence == 0.42 and t.needs_review is True
