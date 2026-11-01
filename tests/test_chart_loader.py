"""chart_loader가 명세대로 동작하는지 확인하는 테스트 (네트워크·DB·.env 없이 실행).

extract_charts() [확인 1~21] (WBS 3.2.13)
테스트용 PDF는 pymupdf로 직접 그려 tmp_path(pytest가 주는 임시 폴더)에 만든다.
- 래스터 차트: 색칠한 PNG 그림을 insert_image로 넣는다 (HRI 차트는 대부분 204×160pt 그림)
- 벡터 차트: 축 2개 + 꺾은선을 선으로 그린다 (LG경영연구원 보고서 차트 모양)
- 표: 격자 선 + 칸 글자 / 글상자: 테두리 + 글 여러 줄 (HRI 요약 쪽·연구진 상자 모양)
작업 폴더를 tmp_path로 바꿔(monkeypatch.chdir) PNG가 저장소의 data/charts/raw/에 쌓이지 않게 한다.

get_reader()·ocr_chart() [확인 22~34] (WBS 3.2.14)
- [확인 22~33]은 진짜 EasyOCR 대신 가짜 Reader(FakeReader)를 끼워 넣어 실행한다 (모델·네트워크 없이 빠르게).
  가짜 Reader의 readtext()는 EasyOCR과 같은 모양 [(네 꼭짓점, 글자, 신뢰도), ...]을 돌려준다.
  [확인 27]의 좌표·글자·신뢰도는 HRI '3%대 성장이 남긴 과제' 10쪽 차트를 진짜 EasyOCR로 읽은 결과 그대로다.
- [확인 34]는 진짜 EasyOCR로 읽는다. 모델 파일이 없으면 건너뛴다 (먼저 get_reader()를 한 번 실행해 내려받기).

chart_to_table()·validate_chart()·chart_to_markdown() [확인 35~51] (WBS 3.2.15)
- Vision 모델: tests/fakes.py의 FakeToolChatModel (with_structured_output에 미리 넣은 ChartData를 돌려주고, 받은 메시지를 기록)
- get_chat_model: 가짜 hri.llm.llm_client 모듈로 바꿔 끼운다 (진짜 모듈은 import할 때 .env를 읽음, test_agent_graph와 같은 방식)
- VISION_MODEL: fake_settings(vision_model=...)로 바꾼다
- [확인 51]의 OCR 토큰은 HRI '3%대 성장이 남긴 과제' 11쪽 차트를 진짜 extract_charts → ocr_chart로 읽은 결과다

실행: poetry run python -m pytest tests/test_chart_loader.py -v
"""

import base64
import os
import sys
import types
from pathlib import Path

import pymupdf
import pytest
from langchain_core.messages import HumanMessage

import hri.loader.chart_loader as chart_loader
from hri.domain.schemas import ChartData, ChartImage, ChartSeries, OcrToken, Settings, Table
from hri.exception.errors import ChartExtractError
from hri.graph.ingest_graph import extract_charts_node, process_charts_node
from hri.loader.chart_loader import (
	VISION_MAX_TOKENS,
	chart_to_markdown,
	chart_to_table,
	extract_charts,
	get_reader,
	ocr_chart,
	validate_chart,
)
from tests.fakes import FakeToolChatModel


# load_settings()는 .env의 MySQL 값이 없으면 오류를 내므로, 테스트에서는 가짜 설정으로 바꿔 끼운다
def fake_settings(chart_dpi=200, min_chart_pt=120, ocr_langs="ko,en", ocr_min_conf=0.4, vision_model="gpt-5.5"):
	return Settings(
		mysql_host="localhost",
		mysql_user="test",
		mysql_password="test",
		mysql_database="test",
		chart_dpi=chart_dpi,
		min_chart_pt=min_chart_pt,
		ocr_langs=ocr_langs,
		ocr_min_conf=ocr_min_conf,
		vision_model=vision_model,
	)


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
	monkeypatch.setattr(chart_loader, "load_settings", lambda: fake_settings())
	monkeypatch.chdir(tmp_path)
	# 테스트끼리 만들어 둔 EasyOCR Reader가 섞이지 않게, 매번 '아직 안 만든 상태'에서 시작한다
	monkeypatch.setattr(chart_loader, "_reader", None)


# 색칠한 PNG 그림 (bytes). 넣을 때 keep_proportion=False로 영역 크기에 딱 맞춘다
def png_bytes(color=(200, 0, 0)):
	pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 40), False)
	pix.set_rect(pix.irect, color)
	return pix.tobytes("png")


def new_pdf(page_count=1):
	doc = pymupdf.open()
	for _ in range(page_count):
		doc.new_page(width=595, height=842)
	return doc


def save(doc, path):
	doc.save(str(path))
	doc.close()
	return str(path)


# 글 한 줄 (fontname="korea": pymupdf에 들어 있는 한글 글꼴)
def write(page, x, y, text, size=9):
	page.insert_text((x, y), text, fontname="korea", fontsize=size)


# HRI 캡션 '< 제목 >': 실제 PDF처럼 꺾쇠는 다른 글꼴(helv 11pt)로 한 줄 안에 이어 쓴다
# → 꺾쇠의 y 좌표가 제목보다 2~3pt 위에 온다 (HRI 부동산 17쪽: '<' y0=544.9, 제목 y0=546.5)
# 글꼴이 다른 글을 한 줄로 쓰려고 TextWriter를 쓴다 (insert_text를 따로 부르면 다른 줄로 읽힘)
def write_hri_caption(page, x, y, title):
	writer = pymupdf.TextWriter(page.rect)
	helv, korea = pymupdf.Font("helv"), pymupdf.Font("korea")
	_, end = writer.append((x, y), "<", font=helv, fontsize=11)
	_, end = writer.append(end, f" {title} ", font=korea, fontsize=9)
	writer.append(end, ">", font=helv, fontsize=11)
	writer.write_text(page)


# 꺾은선 벡터 차트: 왼쪽 위 (x, y), 크기 w×h
def draw_line_chart(page, x, y, w, h):
	page.draw_line((x, y), (x, y + h))
	page.draw_line((x, y + h), (x + w, y + h))
	points = [(x + w * i / 5, y + h * (0.8 - 0.12 * i)) for i in range(6)]
	page.draw_polyline(points, color=(0, 0, 1))
	write(page, x + 5, y + h - 5, "0")
	write(page, x + w - 20, y + 15, "2.4%")


# 격자 표: 왼쪽 위 (x, y), rows×cols 칸, 칸 크기 cw×ch
def draw_table(page, x, y, rows, cols, cw=60, ch=25):
	for r in range(rows + 1):
		page.draw_line((x, y + r * ch), (x + cols * cw, y + r * ch))
	for c in range(cols + 1):
		page.draw_line((x + c * cw, y), (x + c * cw, y + rows * ch))
	for r in range(rows):
		for c in range(cols):
			write(page, x + c * cw + 5, y + r * ch + 16, f"{r}.{c}")


# 글상자: 테두리 + 글 lines줄 (HRI 요약 쪽 · 연구진 상자 모양)
def draw_text_box(page, rect, lines):
	page.draw_rect(rect)
	for i in range(lines):
		write(page, rect.x0 + 5, rect.y0 + 14 + i * 13, "현대경제연구원은 물가 흐름을 점검하고 시사점을 제시하고자 함", size=11)


# ---------------------------------------------------------------------
# 래스터 차트
# ---------------------------------------------------------------------
# [확인 1] 그림 차트 1개 → ChartImage 1개. page는 인덱스+1, bbox는 그림 위치, PNG는 data/charts/raw/{uuid}.png
def test_raster_chart(tmp_path):
	doc = new_pdf(2)
	rect = pymupdf.Rect(90, 500, 294, 660)
	doc[1].insert_image(rect, stream=png_bytes(), keep_proportion=False)
	charts = extract_charts(save(doc, tmp_path / "a.pdf"))

	assert len(charts) == 1
	chart = charts[0]
	assert isinstance(chart, ChartImage)
	assert chart.page == 2
	assert [round(v) for v in chart.bbox] == [90, 500, 294, 660]
	path = Path(chart.image_path)
	assert path.parent.as_posix() == "data/charts/raw"
	assert path.suffix == ".png" and len(path.stem) == 36  # uuid4 문자열 길이
	assert (tmp_path / path).exists()


# [확인 2] CHART_DPI(200)로 자른다: 204pt × 200/72 ≈ 567px
def test_png_size_follows_chart_dpi(tmp_path):
	doc = new_pdf()
	doc[0].insert_image(pymupdf.Rect(90, 500, 294, 660), stream=png_bytes(), keep_proportion=False)
	chart = extract_charts(save(doc, tmp_path / "a.pdf"))[0]

	pix = pymupdf.Pixmap(chart.image_path)
	assert abs(pix.width - 204 * 200 / 72) <= 2
	assert abs(pix.height - 160 * 200 / 72) <= 2


# [확인 3] 가로·세로 MIN_CHART_PT(120pt) 미만은 뺀다 (120pt 딱 맞으면 남긴다)
@pytest.mark.parametrize(
	"width, height, expected",
	[(120, 120, 1), (119, 200, 0), (200, 119, 0), (80, 11, 0)],
)
def test_min_chart_pt(tmp_path, width, height, expected):
	doc = new_pdf()
	doc[0].insert_image(pymupdf.Rect(100, 300, 100 + width, 300 + height), stream=png_bytes(), keep_proportion=False)
	assert len(extract_charts(save(doc, tmp_path / "a.pdf"))) == expected


# [확인 4] 같은 xref 그림이 3쪽 이상 나오면 로고로 보고 뺀다 (2쪽까지는 차트로 남긴다)
@pytest.mark.parametrize("page_count, expected", [(2, 2), (3, 0)])
def test_logo_repeated_xref(tmp_path, page_count, expected):
	doc = new_pdf(page_count)
	rect = pymupdf.Rect(100, 300, 300, 460)
	xref = doc[0].insert_image(rect, stream=png_bytes(), keep_proportion=False)
	for page in list(doc)[1:]:
		page.insert_image(rect, xref=xref)  # 같은 그림을 다시 씀 (xref 같음)
	assert len(extract_charts(save(doc, tmp_path / "a.pdf"))) == expected


# [확인 5] 쪽 전체를 덮는 그림(HRI 표지 배경)은 뺀다
def test_full_page_image_excluded(tmp_path):
	doc = new_pdf()
	doc[0].insert_image(doc[0].rect, stream=png_bytes(), keep_proportion=False)
	assert extract_charts(save(doc, tmp_path / "a.pdf")) == []


# [확인 6] 같은 그림을 한 쪽에 두 번 넣어도 위치마다 1개씩 (같은 xref가 get_images()에 두 번 나와도 중복 없음)
def test_same_image_twice_on_page(tmp_path):
	doc = new_pdf()
	image = png_bytes()
	doc[0].insert_image(pymupdf.Rect(90, 100, 294, 260), stream=image, keep_proportion=False)
	doc[0].insert_image(pymupdf.Rect(90, 400, 294, 560), stream=image, keep_proportion=False)
	charts = extract_charts(save(doc, tmp_path / "a.pdf"))

	assert [round(chart.bbox[1]) for chart in charts] == [100, 400]


# ---------------------------------------------------------------------
# 벡터 차트
# ---------------------------------------------------------------------
# [확인 7] 선으로 그린 차트 → cluster_drawings() 영역으로 잡는다
def test_vector_chart(tmp_path):
	doc = new_pdf()
	draw_line_chart(doc[0], 300, 400, 200, 150)
	charts = extract_charts(save(doc, tmp_path / "a.pdf"))

	assert len(charts) == 1
	x0, y0, x1, y1 = charts[0].bbox
	assert x0 <= 300 and y0 <= 400 and x1 >= 500 and y1 >= 550
	assert Path(charts[0].image_path).exists()


# [확인 8] 작은 벡터 도형(120pt 미만)은 뺀다
def test_small_vector_excluded(tmp_path):
	doc = new_pdf()
	draw_line_chart(doc[0], 300, 400, 100, 150)
	assert extract_charts(save(doc, tmp_path / "a.pdf")) == []


# [확인 9] 선으로 그린 표는 뺀다 (표는 extract_tables 몫)
def test_vector_table_excluded(tmp_path):
	doc = new_pdf()
	draw_table(doc[0], 100, 300, rows=6, cols=4)
	assert extract_charts(save(doc, tmp_path / "a.pdf")) == []


# [확인 10] 글상자(테두리 + 글이 가득한 상자)는 뺀다
def test_text_box_excluded(tmp_path):
	doc = new_pdf()
	draw_text_box(doc[0], pymupdf.Rect(86, 300, 509, 450), lines=10)
	assert extract_charts(save(doc, tmp_path / "a.pdf")) == []


# [확인 11] 그림 차트를 감싼 상자 → 그림 쪽 1개만 (같은 차트를 두 번 자르지 않음)
def test_box_around_raster_chart(tmp_path):
	doc = new_pdf()
	page = doc[0]
	page.draw_rect(pymupdf.Rect(86, 300, 509, 520))
	page.insert_image(pymupdf.Rect(100, 330, 300, 500), stream=png_bytes(), keep_proportion=False)
	charts = extract_charts(save(doc, tmp_path / "a.pdf"))

	assert len(charts) == 1
	assert [round(v) for v in charts[0].bbox] == [100, 330, 300, 500]


# ---------------------------------------------------------------------
# 캡션
# ---------------------------------------------------------------------
# [확인 12] HRI '< 제목 >': 나란히 놓인 두 차트가 각자 위의 캡션을 갖는다. 아래 '자료 :' 줄은 캡션이 아님
def test_hri_captions_side_by_side(tmp_path):
	doc = new_pdf()
	page = doc[0]
	write_hri_caption(page, 110, 490, "소비자물가 상승률")
	write_hri_caption(page, 320, 490, "원/달러 환율 추이")
	page.insert_image(pymupdf.Rect(90, 500, 294, 660), stream=png_bytes(), keep_proportion=False)
	page.insert_image(pymupdf.Rect(303, 500, 507, 660), stream=png_bytes((0, 0, 200)), keep_proportion=False)
	write(page, 92, 675, "자료 : 통계청.")
	charts = extract_charts(save(doc, tmp_path / "a.pdf"))

	assert [chart.caption for chart in charts] == ["< 소비자물가 상승률 >", "< 원/달러 환율 추이 >"]


# [확인 13] 두 줄로 나뉜 캡션은 이어 붙인다
def test_two_line_caption(tmp_path):
	doc = new_pdf()
	page = doc[0]
	write(page, 100, 470, "< 한국의 민간신용(GDP대비)")
	write(page, 100, 485, "비율 추이 >")
	page.insert_image(pymupdf.Rect(90, 500, 294, 660), stream=png_bytes(), keep_proportion=False)
	chart = extract_charts(save(doc, tmp_path / "a.pdf"))[0]

	assert chart.caption == "< 한국의 민간신용(GDP대비) 비율 추이 >"


# [확인 14] 차트보다 넓은 캡션: 앞의 '<'가 차트 왼쪽 밖에 있어도 빠지지 않는다 (HRI 트럼프 노믹스 26쪽 모양)
def test_caption_wider_than_chart(tmp_path):
	doc = new_pdf()
	page = doc[0]
	write(page, 297, 490, "< 대중 수입 관세율 추이 >")  # "<"(297~306pt)의 가운데가 차트 왼쪽(303pt) 밖
	page.insert_image(pymupdf.Rect(303, 500, 507, 660), stream=png_bytes(), keep_proportion=False)
	chart = extract_charts(save(doc, tmp_path / "a.pdf"))[0]

	assert chart.caption == "< 대중 수입 관세율 추이 >"


# [확인 15] 위에 없으면 아래에서 찾는다: '[그림 n] 제목', '<그림 n>'
@pytest.mark.parametrize("text", ["[그림 2-1] 채권 종류별 발행 잔액", "<그림 3>"])
def test_caption_below(tmp_path, text):
	doc = new_pdf()
	page = doc[0]
	page.insert_image(pymupdf.Rect(90, 500, 294, 660), stream=png_bytes(), keep_proportion=False)
	write(page, 100, 680, text)
	chart = extract_charts(save(doc, tmp_path / "a.pdf"))[0]

	assert chart.caption == text


# [확인 16] 아래 캡션 다음 줄의 '자료 :'·'단위' 줄은 캡션에 붙이지 않는다
def test_caption_below_stops_at_line_end(tmp_path):
	doc = new_pdf()
	page = doc[0]
	page.insert_image(pymupdf.Rect(90, 300, 294, 460), stream=png_bytes(), keep_proportion=False)
	write(page, 100, 475, "[그림 1] 소비자물가 추이")
	write(page, 100, 490, "자료 : 통계청, 단위 : %")
	chart = extract_charts(save(doc, tmp_path / "a.pdf"))[0]

	assert chart.caption == "[그림 1] 소비자물가 추이"


# [확인 17] 캡션 모양이 아니거나(출처 줄·빈 꺾쇠) 40pt보다 멀면 None
@pytest.mark.parametrize(
	"text, y", [("자료 : 한국은행.", 490), ("< >", 490), ("< 소비자물가 상승률 >", 450)]
)
def test_no_caption(tmp_path, text, y):
	doc = new_pdf()
	write(doc[0], 100, y, text)
	doc[0].insert_image(pymupdf.Rect(90, 500, 294, 660), stream=png_bytes(), keep_proportion=False)
	assert extract_charts(save(doc, tmp_path / "a.pdf"))[0].caption is None


# ---------------------------------------------------------------------
# 순서 · 설정 · 노드 연결
# ---------------------------------------------------------------------
# [확인 18] 쪽 순서, 쪽 안에서는 위 → 아래 (래스터·벡터가 섞여도)
def test_order(tmp_path):
	doc = new_pdf(2)
	doc[1].insert_image(pymupdf.Rect(90, 100, 294, 260), stream=png_bytes(), keep_proportion=False)
	doc[0].insert_image(pymupdf.Rect(90, 500, 294, 660), stream=png_bytes((0, 200, 0)), keep_proportion=False)
	draw_line_chart(doc[0], 300, 100, 200, 150)  # 1쪽: 그림(아래)을 먼저 넣고 벡터 차트(위)를 나중에 그림
	charts = extract_charts(save(doc, tmp_path / "a.pdf"))

	assert [(chart.page, round(chart.bbox[1])) for chart in charts] == [(1, 100), (1, 500), (2, 100)]


# [확인 19] CHART_DPI·MIN_CHART_PT는 Settings에서 읽는다
def test_settings_used(tmp_path, monkeypatch):
	monkeypatch.setattr(chart_loader, "load_settings", lambda: fake_settings(chart_dpi=100, min_chart_pt=170))
	doc = new_pdf()
	doc[0].insert_image(pymupdf.Rect(90, 100, 294, 260), stream=png_bytes(), keep_proportion=False)  # 204×160 → 170 미만이라 빠짐
	doc[0].insert_image(pymupdf.Rect(90, 400, 290, 580), stream=png_bytes(), keep_proportion=False)  # 200×180
	charts = extract_charts(save(doc, tmp_path / "a.pdf"))

	assert len(charts) == 1
	assert abs(pymupdf.Pixmap(charts[0].image_path).width - 200 * 100 / 72) <= 2


# [확인 20] 차트가 없으면 빈 목록, 폴더·PNG도 만들지 않는다
def test_no_chart(tmp_path):
	doc = new_pdf()
	write(doc[0], 72, 72, "본문만 있는 쪽")
	assert extract_charts(save(doc, tmp_path / "a.pdf")) == []
	assert not (tmp_path / "data/charts/raw").exists()


# [확인 21] extract_charts_node가 진짜 extract_charts를 불러 {'charts': [...]}로 돌려준다 (3.8.2 노드와 연결)
def test_extract_charts_node(tmp_path):
	doc = new_pdf()
	doc[0].insert_image(pymupdf.Rect(90, 500, 294, 660), stream=png_bytes(), keep_proportion=False)
	result = extract_charts_node({"file_path": save(doc, tmp_path / "a.pdf")})

	assert list(result) == ["charts"]
	assert len(result["charts"]) == 1 and result["charts"][0].page == 1


# =====================================================================
# get_reader()·ocr_chart() (WBS 3.2.14)
# =====================================================================
class FakeReader:
	"""EasyOCR Reader 흉내. readtext()는 정해 둔 결과를 돌려주고, 받은 인자를 기록한다."""

	def __init__(self, results=None, error=None):
		self.results = results or []
		self.error = error
		self.calls = []

	def readtext(self, image_path, **options):
		self.calls.append((image_path, options))
		if self.error is not None:
			raise self.error
		return self.results


def box(x0, y0, x1, y1):
	"""EasyOCR이 주는 네 꼭짓점 [[왼위], [오른위], [오른아래], [왼아래]]"""
	return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def use_reader(monkeypatch, reader):
	monkeypatch.setattr(chart_loader, "get_reader", lambda: reader)
	return reader


# [확인 22] get_reader(): Reader(['ko','en'], gpu=False)를 처음 한 번만 만들고, 두 번째부터는 같은 것을 돌려준다
def test_get_reader_made_once(monkeypatch):
	made = []

	class Reader:
		def __init__(self, langs, gpu):
			made.append((langs, gpu))

	monkeypatch.setitem(sys.modules, "easyocr", types.SimpleNamespace(Reader=Reader))

	first = get_reader()
	second = get_reader()

	assert first is second
	assert made == [(["ko", "en"], False)]


# [확인 23] get_reader(): 언어는 Settings.ocr_langs(OCR_LANGS)를 쉼표로 나눠 쓴다 (공백은 지움)
@pytest.mark.parametrize("ocr_langs, langs", [
	("ko, en", ["ko", "en"]),
	("en", ["en"]),
])
def test_get_reader_uses_ocr_langs(monkeypatch, ocr_langs, langs):
	made = []

	class Reader:
		def __init__(self, langs, gpu):
			made.append(langs)

	monkeypatch.setitem(sys.modules, "easyocr", types.SimpleNamespace(Reader=Reader))
	monkeypatch.setattr(chart_loader, "load_settings", lambda: fake_settings(ocr_langs=ocr_langs))

	get_reader()
	assert made == [langs]


# [확인 24] ocr_chart(): readtext(image_path, detail=1)로 읽고, 결과는 OcrToken 목록
def test_ocr_chart_calls_readtext(monkeypatch):
	reader = use_reader(monkeypatch, FakeReader([(box(10, 20, 40, 32), "2.7", 0.91)]))

	tokens = ocr_chart("data/charts/raw/a.png")

	assert reader.calls == [("data/charts/raw/a.png", {"detail": 1})]
	assert tokens == [OcrToken(text="2.7", bbox=(10, 20, 40, 32), conf=0.91, is_number=True)]


# [확인 25] conf < OCR_MIN_CONF(0.4)는 버린다. 0.4와 같으면 남긴다
def test_drop_low_conf(monkeypatch):
	use_reader(monkeypatch, FakeReader([
		(box(0, 0, 10, 10), "버림", 0.39),
		(box(20, 0, 30, 10), "남김", 0.4),
		(box(40, 0, 50, 10), "남김2", 0.95),
	]))
	assert [t.text for t in ocr_chart("a.png")] == ["남김", "남김2"]


# [확인 26] 기준값은 Settings.ocr_min_conf(OCR_MIN_CONF)에서 읽는다
def test_min_conf_from_settings(monkeypatch):
	monkeypatch.setattr(chart_loader, "load_settings", lambda: fake_settings(ocr_min_conf=0.6))
	use_reader(monkeypatch, FakeReader([
		(box(0, 0, 10, 10), "3.0", 0.45),
		(box(20, 0, 30, 10), "3.5", 0.83),
	]))
	assert [t.text for t in ocr_chart("a.png")] == ["3.5"]


# [확인 27] 위→아래, 왼→오른쪽 정렬. 같은 줄인데 높이가 1~2px 다른 x축 라벨도 왼쪽부터 나온다
#          (HRI '3%대 성장이 남긴 과제' 10쪽 차트를 진짜 EasyOCR로 읽은 결과 13개 그대로. 받은 순서만 일부러 섞음)
#          - x축: '2024'(y=414)보다 '2026(E)'(y=412)가 2px 위 → y만으로 정렬하면 2026(E)가 먼저 나온다
#          - '3,5'(y=128)와 '3.4'(y=126)도 같은 줄 → 왼쪽 '3,5'가 먼저
def test_sort_top_to_bottom_left_to_right(monkeypatch):
	use_reader(monkeypatch, FakeReader([
		(box(444, 412, 528, 440), "2027(F)", 0.66),
		(box(10, 0, 50, 24), "4.0", 0.98),
		(box(100, 414, 156, 438), "2024", 0.61),
		(box(466, 126, 504, 150), "3.4", 0.99),
		(box(228, 100, 266, 124), "3.5", 0.99),
		(box(12, 260, 50, 284), "3.0", 0.45),
		(box(324, 412, 408, 440), "2026(E)", 1.0),
		(box(64, 8, 111, 38), "(%)", 0.97),
		(box(10, 128, 50, 154), "3,5", 0.58),
		(box(218, 414, 276, 438), "2025", 1.0),
		(box(348, 230, 386, 254), "3.0", 0.97),
		(box(110, 100, 148, 124), "3.5", 0.83),
		(box(10, 388, 50, 414), "2.5", 0.8),
	]))

	tokens = ocr_chart("a.png")

	assert [t.text for t in tokens] == [
		"4.0", "(%)",
		"3.5", "3.5",
		"3,5", "3.4",
		"3.0",
		"3.0",
		"2.5",
		"2024", "2025", "2026(E)", "2027(F)",
	]
	# 같은 '3.5'도 왼쪽(x=110) 것이 먼저
	assert [t.bbox[0] for t in tokens[2:4]] == [110, 228]
	# 소수점을 쉼표로 읽은 '3,5'는 숫자로 보지 않는다 ([확인 30])
	assert tokens[4].is_number is False


# [확인 28] bbox는 네 꼭짓점의 x·y 최소·최대 (기울어진 글자도)
def test_bbox_from_four_points(monkeypatch):
	use_reader(monkeypatch, FakeReader([([[12, 30], [52, 22], [55, 38], [15, 46]], "기울어짐", 0.9)]))
	assert ocr_chart("a.png")[0].bbox == (12, 22, 55, 46)


# [확인 29] 숫자 정리: 천 단위 쉼표·전각 기호·점 옆 공백을 정리하고 is_number=True
@pytest.mark.parametrize("raw, text", [
	("3.5", "3.5"),
	("-1.0", "-1.0"),
	("2024", "2024"),
	("12,000", "12000"),        # 천 단위 쉼표 (HRI 차트 78토큰)
	("600,000", "600000"),
	("１２．５", "12.5"),         # 전각 숫자·점
	("－３", "-3"),              # 전각 빼기
	("１，２００", "1200"),       # 전각 쉼표
	("61. 7", "61.7"),           # 점 뒤 공백 (HRI 차트에서 읽힌 그대로)
	(" 3.0 ", "3.0"),            # 앞뒤 공백
])
def test_number_cleaned(monkeypatch, raw, text):
	use_reader(monkeypatch, FakeReader([(box(0, 0, 10, 10), raw, 0.9)]))
	token = ocr_chart("a.png")[0]
	assert (token.text, token.is_number) == (text, True)


# [확인 30] 숫자가 아니면 읽은 글자 그대로, is_number=False
#          - '3,5': 소수점을 쉼표로 잘못 읽은 것. 쉼표를 지우면 35가 되므로 숫자로 보지 않는다
#          - '1065 1070', '3,077 3,063': 두 값이 붙어 읽힌 것. 공백을 지우면 다른 수가 되므로 숫자로 보지 않는다
@pytest.mark.parametrize("raw", ["3,5", "52,6", "1065 1070", "3,077 3,063", "(%)", "2026(E)", "'24.M1", "전년동기비"])
def test_not_number_kept_as_is(monkeypatch, raw):
	use_reader(monkeypatch, FakeReader([(box(0, 0, 10, 10), raw, 0.9)]))
	token = ocr_chart("a.png")[0]
	assert (token.text, token.is_number) == (raw, False)


# [확인 31] 글자가 하나도 없으면 빈 목록
def test_no_text(monkeypatch):
	use_reader(monkeypatch, FakeReader([]))
	assert ocr_chart("a.png") == []


# [확인 32] OCR 실패(그림 파일 문제·모델을 못 불러옴)는 ChartExtractError로 바꿔 낸다
#           → process_charts_node가 그 차트만 건너뛰고 warnings에 남긴다
def test_readtext_error(monkeypatch):
	use_reader(monkeypatch, FakeReader(error=ValueError("이미지를 읽을 수 없음")))
	with pytest.raises(ChartExtractError, match="b.png"):
		ocr_chart("b.png")


def test_get_reader_error(monkeypatch):
	def broken():
		raise OSError("모델 내려받기 실패")

	monkeypatch.setattr(chart_loader, "get_reader", broken)
	with pytest.raises(ChartExtractError, match="모델 내려받기 실패"):
		ocr_chart("b.png")


# [확인 33] process_charts_node + 진짜 ocr_chart: OCR이 실패한 차트는 warnings, 나머지는 표로
#           (tests/fakes.py의 가짜 ocr_chart 대신 이 파일의 진짜 ocr_chart를 쓰고, Vision 단계만 가짜)
def test_process_charts_node_with_real_ocr_chart(monkeypatch):
	from hri.graph.ingest_graph import process_charts_node
	from tests.fakes import FakeExtractors, install_extract_stubs

	charts = [
		ChartImage(page=3, caption="[그림 1]", bbox=(0, 0, 200, 200), image_path="ok.png"),
		ChartImage(page=4, caption="[그림 2]", bbox=(0, 0, 200, 200), image_path="broken.png"),
	]
	install_extract_stubs(monkeypatch, FakeExtractors(charts=charts))
	monkeypatch.setattr(chart_loader, "ocr_chart", ocr_chart)  # 가짜를 진짜로 되돌림

	class Reader(FakeReader):
		def readtext(self, image_path, **options):
			if image_path == "broken.png":
				raise ValueError("깨진 그림")
			return [(box(0, 0, 10, 10), "2.0", 0.9)]

	use_reader(monkeypatch, Reader())

	result = process_charts_node({"charts": charts}, {})

	assert [t.caption for t in result["tables"]] == ["[그림 1]"]
	assert len(result["warnings"]) == 1 and "broken.png" in result["warnings"][0]


# [확인 34] 진짜 EasyOCR로 읽기: 숫자·글자 위치를 아는 그림을 만들어 읽는다
#           모델 파일(~/.EasyOCR/model/)이 없으면 건너뜀 → 먼저 get_reader()를 한 번 실행해 내려받는다
MODEL_DIR = os.path.join(os.path.expanduser("~"), ".EasyOCR", "model")
HAS_MODEL = os.path.isfile(os.path.join(MODEL_DIR, "craft_mlt_25k.pth")) and os.path.isfile(os.path.join(MODEL_DIR, "korean_g2.pth"))


@pytest.mark.skipif(not HAS_MODEL, reason="EasyOCR 모델이 없어 건너뜀 (get_reader()를 한 번 실행해 내려받기)")
def test_real_easyocr(tmp_path):
	# 위 줄: '(%)' 왼쪽, '12,000' 오른쪽 / 아래 줄: '2024' 왼쪽, '3.5' 오른쪽
	doc = pymupdf.open()
	page = doc.new_page(width=300, height=160)
	page.insert_text((20, 40), "(%)", fontsize=20)
	page.insert_text((170, 40), "12,000", fontsize=20)
	page.insert_text((20, 130), "2024", fontsize=20)
	page.insert_text((170, 130), "3.5", fontsize=20)
	image_path = str(tmp_path / "chart.png")
	page.get_pixmap(dpi=200).save(image_path)
	doc.close()

	tokens = ocr_chart(image_path)

	assert [(t.text, t.is_number) for t in tokens] == [("(%)", False), ("12000", True), ("2024", True), ("3.5", True)]
	assert all(t.conf >= 0.4 for t in tokens)


# =====================================================================
# chart_to_table()·validate_chart()·chart_to_markdown() (WBS 3.2.15)
# =====================================================================

PNG_BYTES = b"\x89PNG\r\n\x1a\n fake chart image"


# ─────────────────────────────────────────────────────────────
# 가짜 부품
# ─────────────────────────────────────────────────────────────

class FailingModel:
	"""with_structured_output(...).invoke()가 오류를 내는 모델 (Vision 응답이 양식에 맞지 않는 경우 흉내)."""

	def with_structured_output(self, schema):
		return self

	def invoke(self, messages):
		raise ValueError("JSON 양식이 맞지 않음")


class Deps:
	"""가짜 모델·설정과 get_chat_model 호출 인자를 한곳에 모은다."""

	def __init__(self, responses=(), model=None, vision_model="gpt-5.5"):
		self.model = model or FakeToolChatModel(responses=list(responses))
		self.vision_model = vision_model
		self.get_chat_model_kwargs = []

	def get_chat_model(self, **kwargs):
		self.get_chat_model_kwargs.append(kwargs)
		return self.model


def install(monkeypatch, deps):
	llm_client = types.ModuleType("hri.llm.llm_client")
	llm_client.get_chat_model = deps.get_chat_model
	monkeypatch.setitem(sys.modules, "hri.llm.llm_client", llm_client)
	monkeypatch.setattr(chart_loader, "load_settings", lambda: fake_settings(vision_model=deps.vision_model))
	return deps


def make_chart(tmp_path, page=5, caption="[그림 3] 경제성장률 전망", name="chart.png"):
	path = tmp_path / name
	path.write_bytes(PNG_BYTES)
	return ChartImage(page=page, caption=caption, bbox=(50, 100, 300, 280), image_path=str(path))


def token(text, x0=0, y0=0, is_number=True):
	return OcrToken(text=text, bbox=(x0, y0, x0 + 20, y0 + 10), conf=0.9, is_number=is_number)


def make_data(values_a=(1.1, 3.8, 2.2), values_b=(1.5, 2.4, 2.0), x_labels=("2025", "2026(E)", "2027(E)"), **kwargs):
	return ChartData(
		chart_type="bar",
		title="경제성장률 전망",
		unit="%",
		x_labels=list(x_labels),
		series=[ChartSeries(name="경제성장률", values=list(values_a)), ChartSeries(name="민간소비", values=list(values_b))],
		**kwargs,
	)


# OCR이 읽은 글자: 제목·범례·x축 라벨(글자)과 데이터 레이블(숫자)
TOKENS = [
	token("경제성장률 전망", is_number=False),
	token("1.1", 20, 50), token("3.8", 80, 30), token("2.2", 140, 60),
	token("1.5", 40, 55), token("2.4", 100, 45), token("2.0", 160, 50),
	token("2025", 20, 200, is_number=False),
]


# ─────────────────────────────────────────────────────────────
# chart_to_table()
# ─────────────────────────────────────────────────────────────

# [확인 35] HumanMessage 1개 = [지시문+OCR 토큰(글자·좌표) 글, image_url(data:image/png;base64)],
#          get_chat_model(VISION_MODEL, temperature=0, max_tokens=1500).with_structured_output(ChartData) 결과를 그대로 돌려준다
def test_chart_to_table_sends_text_and_image(monkeypatch, tmp_path):
	data = make_data()
	deps = install(monkeypatch, Deps(responses=[data], vision_model="vision-test-model"))
	chart = make_chart(tmp_path)

	assert chart_to_table(chart, TOKENS) is data
	assert deps.get_chat_model_kwargs == [{"model": "vision-test-model", "temperature": 0, "max_tokens": VISION_MAX_TOKENS}]
	assert VISION_MAX_TOKENS == 1500
	assert deps.model.structured_schemas == [ChartData]

	[messages] = deps.model.calls
	[message] = messages
	assert isinstance(message, HumanMessage)
	text_part, image_part = message.content
	assert text_part["type"] == "text"
	assert "3.8 (80, 30, 100, 40)" in text_part["text"]           # OCR 글자와 좌표
	assert "캡션: [그림 3] 경제성장률 전망" in text_part["text"]
	for rule in ("OCR 글자 중 숫자", "needs_review를 true", "범례에 없는 계열 이름은 만들지 않습니다"):
		assert rule in text_part["text"]                              # 명세서 규칙 3개
	assert image_part["type"] == "image_url"
	url = image_part["image_url"]["url"]
	assert url.startswith("data:image/png;base64,")
	assert base64.b64decode(url.split(",", 1)[1]) == PNG_BYTES       # 그림 파일 내용 그대로


# [확인 36] OCR 글자가 없어도 호출은 하고, 지시문에 '(읽은 글자 없음)'을 넣는다 / 캡션이 없으면 캡션 줄을 넣지 않는다
def test_chart_to_table_without_tokens_and_caption(monkeypatch, tmp_path):
	deps = install(monkeypatch, Deps(responses=[make_data()]))
	chart_to_table(make_chart(tmp_path, caption=None), [])
	text = deps.model.calls[0][0].content[0]["text"]
	assert "(읽은 글자 없음)" in text
	assert "캡션:" not in text


# [확인 37] 실패하면 ChartExtractError ('n쪽 차트: …'), 원인 예외는 __cause__에 남는다
def test_chart_to_table_vision_error(monkeypatch, tmp_path):
	install(monkeypatch, Deps(model=FailingModel()))
	with pytest.raises(ChartExtractError, match="^5쪽 차트: Vision 응답을 ChartData로 바꾸지 못함") as error:
		chart_to_table(make_chart(tmp_path), TOKENS)
	assert isinstance(error.value.__cause__, ValueError)


# [확인 38] 그림 파일이 없으면 Vision을 부르지 않고 ChartExtractError
def test_chart_to_table_missing_image(monkeypatch, tmp_path):
	deps = install(monkeypatch, Deps(responses=[make_data()]))
	chart = ChartImage(page=7, caption=None, bbox=(0, 0, 200, 200), image_path=str(tmp_path / "없는파일.png"))
	with pytest.raises(ChartExtractError, match="^7쪽 차트: 그림 파일을 읽지 못함"):
		chart_to_table(chart, TOKENS)
	assert deps.get_chat_model_kwargs == []


# [확인 39] VISION_MODEL이 비어 있으면('VISION_MODEL=') Vision을 부르지 않고 ChartExtractError
#          (get_chat_model('')은 LLM_MODEL로 바뀌어 다른 모델이 불리므로 막는다)
def test_chart_to_table_empty_vision_model(monkeypatch, tmp_path):
	deps = install(monkeypatch, Deps(responses=[make_data()], vision_model=""))
	with pytest.raises(ChartExtractError, match="VISION_MODEL"):
		chart_to_table(make_chart(tmp_path), TOKENS)
	assert deps.get_chat_model_kwargs == []


# [확인 40] 응답에 x축 라벨이나 계열이 없으면 표로 만들 것이 없으므로 ChartExtractError
@pytest.mark.parametrize("data", [
	ChartData(chart_type="other", x_labels=[], series=[ChartSeries(name="a", values=[])]),
	ChartData(chart_type="other", x_labels=["2025"], series=[]),
])
def test_chart_to_table_empty_result(monkeypatch, tmp_path, data):
	install(monkeypatch, Deps(responses=[data]))
	with pytest.raises(ChartExtractError, match="x축 라벨이나 계열이 없음"):
		chart_to_table(make_chart(tmp_path), TOKENS)


# ─────────────────────────────────────────────────────────────
# validate_chart()
# ─────────────────────────────────────────────────────────────

# [확인 41] 값 6개가 모두 OCR 숫자에 있음 → confidence 1.0, needs_review False. 받은 data는 바꾸지 않는다
def test_validate_chart_all_matched():
	data = make_data()
	result = validate_chart(data, TOKENS)
	assert (result.confidence, result.needs_review) == (1.0, False)
	assert (data.confidence, data.needs_review) == (None, False)
	assert result.series == data.series


# [확인 42] match_ratio = OCR에 있는 값 / 전체 값. 0.5 미만이면 needs_review (0.5는 통과)
@pytest.mark.parametrize("values_b, ratio, needs_review", [
	((1.5, 2.4, 9.9), 5 / 6, False),
	((9.7, 9.8, 9.9), 3 / 6, False),   # 딱 0.5 → 통과
	((1.5, 9.8, 9.9), 4 / 6, False),
])
def test_validate_chart_match_ratio(values_b, ratio, needs_review):
	result = validate_chart(make_data(values_b=values_b), TOKENS)
	assert result.confidence == pytest.approx(ratio)
	assert result.needs_review is needs_review


def test_validate_chart_low_ratio_needs_review():
	result = validate_chart(make_data(values_a=(9.1, 9.2, 2.2), values_b=(9.7, 9.8, 9.9)), TOKENS)
	assert result.confidence == pytest.approx(1 / 6)
	assert result.needs_review is True


# [확인 43] 계열 값 개수 ≠ x축 라벨 개수면 숫자가 다 맞아도 needs_review
def test_validate_chart_count_mismatch():
	result = validate_chart(make_data(values_b=(1.5, 2.4)), TOKENS)
	assert result.confidence == 1.0
	assert result.needs_review is True


# [확인 44] chart_to_table에서 이미 needs_review=True(눈금 근사값)면 그대로 유지
def test_validate_chart_keeps_needs_review():
	result = validate_chart(make_data(needs_review=True), TOKENS)
	assert (result.confidence, result.needs_review) == (1.0, True)


# [확인 45] 못 읽은 값(None)은 비율에서 빼고, 숫자가 아닌 토큰(is_number=False)은 비교하지 않는다
def test_validate_chart_ignores_none_and_non_numbers():
	tokens = [token("1.1"), token("3.8", is_number=False)]
	result = validate_chart(make_data(values_a=(1.1, None, None), values_b=(None, 3.8, 2.2)), tokens)
	assert result.confidence == pytest.approx(1 / 3)   # 1.1만 맞음 (3.8은 숫자 토큰 아님, 2.2는 OCR에 없음)
	assert result.needs_review is True


# [확인 46] 값이 하나도 없으면(모두 None) confidence 0.0, needs_review
def test_validate_chart_no_values():
	result = validate_chart(make_data(values_a=(None, None, None), values_b=(None, None, None)), TOKENS)
	assert (result.confidence, result.needs_review) == (0.0, True)


# ─────────────────────────────────────────────────────────────
# chart_to_markdown()
# ─────────────────────────────────────────────────────────────

# [확인 47] x축 라벨이 행, 계열이 열. 단위는 왼쪽 위 칸, 값은 str() 그대로(19.0 → '19.0'), 못 읽은 값은 빈칸.
#           table_to_markdown()으로 만들고 Table(source_type='chart', image_path, confidence, needs_review)
def test_chart_to_markdown(tmp_path):
	chart = make_chart(tmp_path)
	data = make_data(values_a=(1.1, 3.8, None), values_b=(19.0, 11.0, 2.0)).model_copy(update={"confidence": 0.8, "needs_review": True})
	table = chart_to_markdown(data, chart)

	assert isinstance(table, Table)
	assert table.rows == [
		["구분 (단위: %)", "경제성장률", "민간소비"],
		["2025", "1.1", "19.0"],
		["2026(E)", "3.8", "11.0"],
		["2027(E)", "", "2.0"],
	]
	assert table.markdown == (
		"| 구분 (단위: %) | 경제성장률 | 민간소비 |\n"
		"|---|---|---|\n"
		"| 2025 | 1.1 | 19.0 |\n"
		"| 2026(E) | 3.8 | 11.0 |\n"
		"| 2027(E) |  | 2.0 |"
	)
	assert (table.source_type, table.page, table.caption, table.image_path) == ("chart", 5, "[그림 3] 경제성장률 전망", chart.image_path)
	assert (table.confidence, table.needs_review, table.summary) == (0.8, True, None)


# [확인 48] 캡션이 없으면 차트 안 제목을 캡션으로, 단위가 없으면 왼쪽 위 칸은 '구분'.
#           값이 라벨보다 적은 계열은 모자란 칸을 빈칸으로 채운다 (개수 불일치는 validate_chart가 표시)
def test_chart_to_markdown_without_caption_and_unit(tmp_path):
	data = make_data(values_b=(1.5,)).model_copy(update={"unit": None})
	table = chart_to_markdown(data, make_chart(tmp_path, caption=None))
	assert table.caption == "경제성장률 전망"
	assert table.rows[0][0] == "구분"
	assert [row[2] for row in table.rows[1:]] == ["1.5", "", ""]


# [확인 49] 셀 정리는 table_to_markdown이 한다: 라벨·계열 이름 속 '|'·줄바꿈
def test_chart_to_markdown_reuses_table_to_markdown(tmp_path):
	data = ChartData(chart_type="line", x_labels=["1월\n2026"], series=[ChartSeries(name="수출|수입", values=[-2.5])])
	table = chart_to_markdown(data, make_chart(tmp_path))
	assert table.markdown.splitlines() == ["| 구분 | 수출\\|수입 |", "|---|---|", "| 1월 2026 | -2.5 |"]


# ─────────────────────────────────────────────────────────────
# 차트 노드에서 세 함수를 진짜로 연결 (process_charts_node, 3.8.2)
# ─────────────────────────────────────────────────────────────

# [확인 50] OCR(가짜) → chart_to_table → validate_chart → chart_to_markdown 진짜 함수.
#           성공한 차트는 Table로, Vision 실패 차트는 warnings로 ('n쪽 차트: …')
def test_process_charts_node_with_real_functions(monkeypatch, tmp_path):
	ok_chart = make_chart(tmp_path, page=5, name="ok.png")
	bad_chart = make_chart(tmp_path, page=9, caption=None, name="bad.png")
	low_match = make_data(values_a=(9.1, 9.2, 9.3), values_b=(9.4, 9.5, 2.0))

	class PerChartModel:
		"""그림 파일에 따라 응답을 고른다 (batch가 동시에 부르므로 순서에 기대지 않음)."""

		def with_structured_output(self, schema):
			return self

		def invoke(self, messages):
			url = messages[0].content[1]["image_url"]["url"]
			if "bad" in base64.b64decode(url.split(",", 1)[1]).decode("latin-1"):
				raise ValueError("양식 오류")
			return low_match

	(tmp_path / "bad.png").write_bytes(b"bad image")
	install(monkeypatch, Deps(model=PerChartModel()))
	monkeypatch.setattr(chart_loader, "ocr_chart", lambda image_path: TOKENS)

	result = process_charts_node({"charts": [ok_chart, bad_chart]}, {})

	[table] = result["tables"]
	assert (table.source_type, table.page, table.caption) == ("chart", 5, "[그림 3] 경제성장률 전망")
	assert table.confidence == pytest.approx(1 / 6)
	assert table.needs_review is True
	assert table.markdown.startswith("| 구분 (단위: %) | 경제성장률 | 민간소비 |")
	assert len(result["warnings"]) == 1
	assert result["warnings"][0].startswith("9쪽 차트: Vision 응답을 ChartData로 바꾸지 못함")


# ─────────────────────────────────────────────────────────────
# 실제 HRI 차트 1개로 검수·변환 (발표 예시와 같은 차트)
# ─────────────────────────────────────────────────────────────

# '3%대 성장이 남긴 과제…' 11쪽 < 미국 경제성장률 추이 및 전망 > (IMF·IB 꺾은선 2개, 데이터 레이블 있음)
# 아래 토큰은 이 PDF를 진짜 extract_charts()로 잘라 진짜 ocr_chart()로 읽은 결과 그대로다 (좌표 px, conf는 소수 둘째 자리까지)
# - IB 2027(E) 레이블 '2.1'을 '2'로 읽었다 / 2025의 두 점은 겹쳐 레이블 '2.1'이 1개뿐이다
# - y축 눈금(2.0~2.4)도 숫자 토큰으로 들어온다 → match_ratio에서 눈금 숫자와 같은 값도 '있음'으로 센다 (명세 그대로, 한계)
HRI_P11_TOKENS = [
	OcrToken(text="2.4", bbox=(8, 0, 46, 22), conf=0.54, is_number=True),
	OcrToken(text="(%)", bbox=(66, 12, 110, 38), conf=0.87),
	OcrToken(text="IMF", bbox=(482, 22, 526, 46), conf=1.0),
	OcrToken(text="2.3", bbox=(284, 68, 322, 92), conf=0.81, is_number=True),
	OcrToken(text="IB", bbox=(469, 83, 505, 103), conf=0.96),
	OcrToken(text="2.3", bbox=(8, 96, 46, 120), conf=1.0, is_number=True),
	OcrToken(text="2.2", bbox=(444, 164, 480, 188), conf=0.98, is_number=True),
	OcrToken(text="2.2", bbox=(8, 194, 46, 218), conf=0.67, is_number=True),
	OcrToken(text="2.2", bbox=(284, 226, 322, 250), conf=0.94, is_number=True),
	OcrToken(text="2.1", bbox=(126, 262, 162, 286), conf=0.96, is_number=True),
	OcrToken(text="2.1", bbox=(8, 292, 44, 316), conf=0.8, is_number=True),
	OcrToken(text="2", bbox=(443, 325, 463, 347), conf=1.0, is_number=True),
	OcrToken(text="2.0", bbox=(8, 390, 46, 414), conf=0.86, is_number=True),
	OcrToken(text="2025", bbox=(116, 414, 174, 438), conf=1.0, is_number=True),
	OcrToken(text="2026(E)", bbox=(262, 412, 346, 438), conf=1.0),
	OcrToken(text="2027(E)", bbox=(420, 412, 504, 438), conf=1.0),
]


# [확인 51] 그림과 같은 값(정답)을 넣으면 OCR 숫자에 모두 있어 confidence 1.0, 표는 연도가 행·IMF/IB가 열
def test_hri_chart_example(tmp_path):
	chart = make_chart(tmp_path, page=11, caption="< 미국 경제성장률 추이 및 전망 >")   # extract_charts가 찾은 캡션 그대로
	answer = ChartData(
		chart_type="line", unit="%", x_labels=["2025", "2026(E)", "2027(E)"],
		series=[ChartSeries(name="IMF", values=[2.1, 2.3, 2.2]), ChartSeries(name="IB", values=[2.1, 2.2, 2.1])],
	)
	table = chart_to_markdown(validate_chart(answer, HRI_P11_TOKENS), chart)
	assert (table.confidence, table.needs_review) == (1.0, False)
	assert table.markdown == (
		"| 구분 (단위: %) | IMF | IB |\n"
		"|---|---|---|\n"
		"| 2025 | 2.1 | 2.1 |\n"
		"| 2026(E) | 2.3 | 2.2 |\n"
		"| 2027(E) | 2.2 | 2.1 |"
	)
