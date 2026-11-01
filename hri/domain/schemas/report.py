"""보고서 스키마 · Page, Section, Chunk, Table, Report, ValidationResult, ChartImage, OcrToken, ChartSeries, ChartData (엄정현)

DB에 저장하는 모델(Section·Chunk·Table·Report)의 필드 이름은 sql/schema.sql 컬럼 이름과 같게 둔다.
	- Section → sections / Chunk → chunks / Table → report_tables / Report → reports
	- Page·ValidationResult·ChartImage·OcrToken·ChartSeries·ChartData는 저장하지 않고 업로드(ingest_graph) 중에만 쓴다.
	
PDF 보고서 업로드 검증부터 텍스트/표/차트 추출, 섹션 분할, 청크 생성 및 DB/벡터스토어 저장에 사용되는 핵심 데이터 규격입니다.
"""

from datetime import date
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

__all__ = [
	"Page",
	"Section",
	"Chunk",
	"Table",
	"Report",
	"ValidationResult",
	"ChartImage",
	"OcrToken",
	"ChartSeries",
	"ChartData",
]

# ---------------------------------------------------------------------
# 1. 쪽 · 섹션
# ---------------------------------------------------------------------
class Page(BaseModel):
	"""PDF 한 쪽. 업로드 중 IngestState['pages']에만 있고 DB에는 저장하지 않는다.

	load_pdf()가 만들고 clean_text()(머리말·꼬리말 제거) → find_body_start()(목차 앞 자르기)를 거쳐
	split_sections()의 입력이 된다. 표·차트는 extract_tables()·extract_charts()가 따로 뽑으므로 Page에 담지 않는다.
	"""

	page: int = Field(ge=1, description="PDF 기준 쪽 번호, 1부터")
	text: str = Field(default="", description="추출한 글 (clean_text 뒤에는 머리말·꼬리말 제거)")
	is_scanned: bool = Field(default=False, description="글자가 없는 스캔본·빈 쪽이면 True (load_text 노드가 warnings에 기록)")


class Section(BaseModel):
	"""목차 단위 부모 섹션 (sections 테이블 1행). split_sections()가 만든다.

	답변할 때 get_context_docs()가 텍스트 청크의 (report_id, section_no)로 이 섹션 원문을 근거로 넣는다
	(같은 섹션은 1번만, 섹션당 MAX_CONTEXT_CHARS 상한).
	"""

	report_id: int | None = Field(default=None, description="insert_report() 뒤에 채움")
	section_no: int = Field(
	ge=0,
	description="보고서 내부 순번, 0부터 (보고서마다 0부터 다시 시작, report_id와 묶어 PK). 인쇄된 장 번호와 다름",
)
	title: str = Field(max_length=255, description="섹션 제목. 예) 1. 개요, Ⅰ. 최근 국내외 경제 동향")
	start_page: int = Field(ge=1, description="시작 쪽, 1부터")
	end_page: int = Field(ge=1, description="끝 쪽 (start_page 이상인지는 DB CHECK ck_sections_pages가 확인)")
	text: str = Field(description="섹션 원문 (답변 근거)")


# ---------------------------------------------------------------------
# 2. 청크 · 표
# ---------------------------------------------------------------------
class Chunk(BaseModel):
	"""검색 단위 자식 청크 (chunks 테이블 1행 = Chroma 도큐먼트 1개). Chroma id = '{report_id}-{chunk_no}'.

	text 청크: 본문 조각, doc_id = None
	table·chart 청크: text = 캡션+요약, doc_id로 report_tables의 원본 표를 가리킴
	(이 짝이 맞는지는 schema.sql의 ck_chunks_doc_id가 확인한다)
	split 단계에서는 report_id가 None이다. persist 노드에서 insert_report() 결과로 채운 뒤 add_chunks()를 호출할 것
	(None이면 Chroma id가 'None-0'이 되고, 메타데이터에서 빠져 delete_report_vectors()가 지우지 못함).
	"""

	report_id: int | None = Field(default=None, description="insert_report() 뒤에 채움")
	chunk_no: int = Field(
		ge=0,
		description="보고서 내부 순번, 0부터 (보고서마다 0부터 다시 시작, report_id와 묶어 PK · Chroma id '{report_id}-{chunk_no}')",
	)
	section_no: int = Field(ge=0, description="속한 부모 섹션의 보고서 내부 순번 (report_id와 묶어 sections FK)")
	page: int = Field(ge=1, description="출처 쪽, 1부터")
	chunk_type: Literal["text", "table", "chart"] = Field(
		default="text",
		description="text: 본문 조각 / table: 표 요약 / chart: 차트 요약 (출처에 '차트 OCR'로 표시)",
	)
	category: str | None = Field(default=None, max_length=50, description="분류 (없으면 None)")
	text: str = Field(min_length=1, description="임베딩·검색 대상. 본문, 또는 표·차트의 캡션+요약")
	doc_id: str | None = Field(
		default=None,
		max_length=36,
		description="표·차트 청크만: report_tables.doc_id. 텍스트 청크는 반드시 None (DB CHECK ck_chunks_doc_id)",
	)


class Table(BaseModel):
	"""보고서 속 표·차트 원본 (report_tables 테이블 1행).

	표: extract_tables()가 rows로 만들고, 표 노드가 table_to_markdown()으로 markdown을 채운다.
	차트: chart_to_markdown()이 source_type='chart'로 만든다.
	summary(검색용)는 summarize_tables()가 채우고, 답변에는 markdown(원본)을 쓴다.
	confidence는 DB의 ocr_confidence 칸에 저장한다 (insert_tables에서 이름을 바꿔 넣음).
	doc_id는 만들 때마다 새 uuid가 들어간다. 큰 표 조각을 model_copy()로 만들면 doc_id가 복사되므로 새로 만들 것.
	"""

	doc_id: str = Field(default_factory=lambda: str(uuid4()), description="uuid, report_tables PK · chunks.doc_id가 참조")
	report_id: int | None = Field(default=None, description="insert_report() 뒤에 채움")
	page: int = Field(ge=1, description="표·차트가 있는 쪽, 1부터")
	caption: str | None = Field(default=None, description="표 제목. 예) < 주요국 경제성장률 전망 >. 큰 표를 나누면 '(1/n)'을 붙임")
	rows: list[list[str | None]] = Field(default_factory=list, description="머리글 행 + 본문 행. 병합된 칸 자리는 None(pdfplumber 그대로, validate_table이 병합 의심 칸으로 셈). DB에는 저장 안 함")
	markdown: str = Field(default="", description="table_to_markdown() 결과, 답변용 원본")
	summary: str | None = Field(default=None, description="검색에 쓰는 1~2문장 요약 (summarize_tables), 표·차트 청크의 text가 됨")
	source_type: Literal["table", "chart"] = "table"
	image_path: str | None = Field(default=None, description="차트만: data/charts/raw/의 PNG 경로, 표는 None")
	confidence: float | None = Field(default=None, ge=0, le=1, description="차트만: match_ratio (validate_chart), 표는 None")
	needs_review: bool = Field(default=False, description="True면 답변 출처에 '추정값' 표시")


# ---------------------------------------------------------------------
# 3. 보고서 · 검증 결과
# ---------------------------------------------------------------------
class Report(BaseModel):
	"""업로드한 보고서 (reports 테이블 1행).

	제목·발행기관·발행일은 extract_report_meta()가 PDF 메타 → 첫 쪽 → 파일명 순으로 채운다.
	chunk_count는 DB 칸이 없어 chunks를 집계해서 채운다.
	summary_json(요약 캐시)은 ReportSummary로 따로 다루고, created_at은 DB가 채우므로 이 모델에 넣지 않는다.
	"""

	report_id: int | None = Field(default=None, description="AUTO_INCREMENT, insert_report()가 돌려줌. 저장 전에는 None")
	title: str = Field(min_length=1, max_length=255, description="보고서 제목 (PDF 메타 → 첫 쪽 → 파일명 순으로 찾음)")
	publisher: str | None = Field(default=None, max_length=100, description="발행기관. PDF 메타·첫 쪽·파일명에서 찾지 못하면 None(화면에는 '확인할 수 없는 발행기관'으로 표시)")
	published_date: date | None = Field(default=None, description="발행일. '2026-07-31' 글자도 받음, 못 찾으면 None")
	page_count: int = Field(default=0, ge=0, description="PDF 전체 쪽 수")
	chunk_count: int = Field(default=0, ge=0, description="청크 수 (DB 칸 없음, chunks를 집계해서 채움)")
	file_path: str = Field(max_length=255, description="data/raw/에 저장한 PDF 경로. 예) data/raw/sample.pdf")
	uploaded_by: str = Field(max_length=50, description="업로드한 user_id (users FK)")
	file_hash: str = Field(
		default_factory=lambda: str(uuid4()),
		min_length=36,
		max_length=36,
		description="uuid 36자, reports UK. Report를 만들 때 자동으로 채워짐 (DB에서 읽은 행은 그 값을 그대로 씀)",
	)


class ValidationResult(BaseModel):
	"""[PDF 검증 결과] 업로드된 PDF 파일의 적합성 검사 결과. 예외를 던지지 않고 이 값을 돌려준다.
    확인 항목: 확장자(.pdf) · 용량(MAX_MB=30) · 쪽 수(MAX_PAGES=150) · pypdf로 열리는지 · 암호화 여부
	실패하면 validate 노드가 reason을 state['error']에 기록하고, on_upload()가 화면에 보여 준다.
	글자가 없는 스캔본은 여기서 막지 않는다 (load_pdf()가 Page.is_scanned로 표시)."""

	ok: bool = Field(description="통과하면 True")
	reason: str | None = Field(default=None, description="실패 사유 (화면에 그대로 표시). 예) 'PDF 파일만 업로드할 수 있습니다'")


# ---------------------------------------------------------------------
# 4. 차트 (저장 안 함 → chart_to_markdown()이 Table로 바꿔서 저장)
# ---------------------------------------------------------------------
class ChartImage(BaseModel):
	"""extract_charts()가 PDF에서 잘라낸 차트 한 개. ocr_chart()·chart_to_table()의 입력이 된다.

	가로·세로 MIN_CHART_PT(120pt) 미만과 여러 쪽에 반복되는 로고는 제외하고,
	CHART_DPI(200)로 잘라 data/charts/raw/에 uuid 파일명 PNG로 저장한다.
	"""

	page: int = Field(ge=1, description="차트가 있는 쪽, 1부터 (fitz 인덱스+1)")
	caption: str | None = Field(default=None, description="'[그림 n]'·'<그림 n>'·'[차트 n]' 캡션, 못 찾으면 None")
	bbox: tuple[float, float, float, float] = Field(description="쪽 위 영역 [x0, y0, x1, y1] (pt)")
	image_path: str = Field(description="data/charts/raw/의 PNG 경로 (uuid 파일명)")


class OcrToken(BaseModel):
	"""ocr_chart()가 읽은 글자 한 덩어리 (제목·축 눈금·범례·데이터 레이블 후보).
	conf < OCR_MIN_CONF(0.4)인 토큰은 버리고, 숫자는 쉼표·공백·전각 기호를 정리해 is_number=True로 표시한다.
	목록은 위→아래, 왼→오른쪽 순으로 정렬한다."""

	text: str = Field(description="읽은 글자. 숫자는 쉼표·공백·전각 기호를 정리한 값")
	bbox: tuple[float, float, float, float] = Field(
		description="이미지 위 영역 (x0, y0, x1, y1) px. EasyOCR 네 꼭짓점에서 x·y의 최소·최대로 변환",
	)
	conf: float = Field(ge=0, le=1, description="EasyOCR 신뢰도")
	is_number: bool = Field(default=False, description="숫자 토큰이면 True")


class ChartSeries(BaseModel):
	"""차트의 계열 하나 (범례 한 줄).
	values는 ChartData.x_labels와 같은 순서·같은 개수여야 한다 (validate_chart()가 확인)
	"""

	name: str = Field(description="계열 이름 (범례에 있는 이름만)")
	values: list[float | None] = Field(description="x축 라벨 순서대로의 값. 못 읽은 칸은 None")


class ChartData(BaseModel):
	"""chart_to_table()이 Vision LLM으로 복원한 차트 데이터 (with_structured_output 대상).

	OCR 토큰과 차트 이미지를 함께 보내 채우고, chart_to_markdown()이 Table(source_type='chart')로 바꾼다.
	confidence는 LLM이 아니라 validate_chart()가 match_ratio로 채운다.
	"""

	chart_type: Literal["bar", "line", "pie", "mixed", "other"] = Field(description="차트 유형")
	title: str | None = Field(default=None, description="차트 안 제목, 없으면 None")
	unit: str | None = Field(default=None, description="단위. 예) %, 억 원, 달러. 없으면 None")
	x_labels: list[str] = Field(description="x축 라벨 (표의 행). 예) ['2025Q1', '2025Q2', '2025Q3']")
	series: list[ChartSeries] = Field(description="계열 목록 (표의 열)")
	needs_review: bool = Field(
		default=False,
		description="True: 눈금으로 읽은 근사값이 있거나, match_ratio < 0.5이거나, 값 개수가 x_labels와 다름",
	)
	confidence: float | None = Field(
		default=None, ge=0, le=1,
		description="match_ratio: LLM이 낸 숫자 중 OCR 토큰에 실제로 있는 비율 (validate_chart)",
	)

