"""차트 데이터 추출 · extract_charts, ocr_chart, chart_to_table, validate_chart, chart_to_markdown (엄정현)"""

# 엄정현 extract_charts() 추가 (WBS 3.2.13), get_reader()·ocr_chart() 추가 (WBS 3.2.14),
# chart_to_table()·validate_chart()·chart_to_markdown() 추가 (WBS 3.2.15).
# extract_charts_node()(graph/ingest_graph)가 부르고, 돌려준 ChartImage 목록은 process_charts_node()가
# ocr_chart() → chart_to_table() → validate_chart() → chart_to_markdown() 순서로 표(Table)로 바꾼다.
# ocr_chart()는 process_charts_node()가 차트마다 **하나씩 차례로** 부른다.
# 읽은 글자(OcrToken 목록)는 chart_to_table()이 Vision LLM에 그림과 함께 넘기고,
# validate_chart()가 LLM이 돌려준 숫자가 OCR 숫자에 있는지 비교할 때 쓴다.

import base64
import re
import unicodedata
import uuid
from collections import defaultdict
from pathlib import Path

import pymupdf
from langchain_core.messages import HumanMessage

from hri.config.settings import load_settings
from hri.domain.schemas import ChartData, ChartImage, OcrToken, Table
from hri.exception.errors import ChartExtractError
from hri.loader.table_loader import table_to_markdown

# ---------------------------------------------------------------------
# extract_charts()에서 쓰는 값
# ---------------------------------------------------------------------
# 잘라 낸 차트 PNG를 저장하는 폴더 (앱을 실행하는 저장소 폴더 기준, .gitignore에 들어 있음)
CHART_DIR = Path("data/charts/raw")

# 같은 그림(xref)이 이만큼 이상의 쪽에 나오면 로고로 보고 뺀다 (명세서)
LOGO_MIN_PAGES = 3

# 쪽 넓이의 90% 이상을 덮는 영역은 차트가 아니라 쪽 전체로 보고 뺀다
# (HRI 37편 표지 배경 그림 37개, 스캔한 쪽, 쪽 전체를 감싼 도형 묶음)
FULL_PAGE_RATIO = 0.9

# 벡터 도형 묶음을 뺄 때 쓰는 값 (10/28 결정, HRI 37편 · 타사 10편 점검)
# - 래스터 차트 넓이의 절반 이상이 묶음 안에 있으면: 그림 차트를 감싼 상자 → 그림 쪽만 남긴다
# - find_tables()가 찾은 표가 묶음 넓이의 절반 이상이면: 표 → extract_tables()(pdfplumber)가 가져간다
OVERLAP_RATIO = 0.5
# - 글자가 차지하는 넓이가 묶음의 20% 이상이면: 글상자·요약 쪽 테두리 (차트는 눈금·범례 글자뿐이라 20% 미만)
TEXT_BOX_RATIO = 0.2

# 캡션을 찾는 범위: 영역 위·아래 40pt 안 (HRI 캡션 마지막 줄은 그림 위 2~22pt, 두 줄 캡션의 첫 줄은 36pt까지)
CAPTION_GAP = 40

# 캡션: '[그림 n] 제목', '[차트 n] 제목'(그 줄 끝까지) / '<그림 n>', HRI '< 제목 >'(꺾쇠 안 전체, 두 줄이어도 됨)
# '< >'처럼 빈 꺾쇠는 캡션이 아님 (table_loader.CAPTION과 같은 기준)
CAPTION_PATTERN = re.compile(r"\[\s*(?:그림|차트)\s*[\d\-.]+\s*\][^\[\]<>\n]*|<\s*[^<>\s][^<>]*>")


def _is_big(rect: pymupdf.Rect, min_pt: int) -> bool:
	"""가로·세로가 모두 MIN_CHART_PT 이상이면 True"""
	return rect.width >= min_pt and rect.height >= min_pt


def _logo_xrefs(doc: pymupdf.Document) -> set[int]:
	"""LOGO_MIN_PAGES(3)쪽 이상에 나오는 그림의 xref (로고·머리말 그림)"""
	pages_of = defaultdict(set)
	for page in doc:
		for image in page.get_images(full=True):
			xref = image[0]
			# 위치가 없는 xref는 다른 그림의 투명도 마스크(smask)라 쪽에 그려지지 않는다
			if page.get_image_rects(xref):
				pages_of[xref].add(page.number)
	return {xref for xref, pages in pages_of.items() if len(pages) >= LOGO_MIN_PAGES}


def _raster_rects(page: pymupdf.Page, logos: set[int], min_pt: int) -> list[pymupdf.Rect]:
	"""① 래스터 차트: get_images(full=True)의 그림마다 get_image_rects(xref)로 위치를 얻는다"""
	page_area = page.rect.get_area()
	rects = []
	# 같은 그림을 한 쪽에 두 번 쓰면 get_images()에 같은 xref가 두 번 나오고,
	# get_image_rects(xref)는 그때마다 두 위치를 모두 돌려주므로 xref를 한 번씩만 본다
	xrefs = dict.fromkeys(image[0] for image in page.get_images(full=True))
	for xref in xrefs:
		if xref in logos:
			continue
		for rect in page.get_image_rects(xref):
			if not _is_big(rect, min_pt):
				continue
			if rect.get_area() >= page_area * FULL_PAGE_RATIO:
				continue
			rects.append(rect)
	return rects


def _text_ratio(words: list, rect: pymupdf.Rect) -> float:
	"""rect 안 낱말 상자 넓이의 합 / rect 넓이"""
	text_area = sum(pymupdf.Rect(word[:4]).get_area() for word in words if pymupdf.Rect(word[:4]) in rect)
	return text_area / rect.get_area()


def _vector_rects(
	page: pymupdf.Page, rasters: list[pymupdf.Rect], words: list, min_pt: int
) -> list[pymupdf.Rect]:
	"""② 벡터 차트: cluster_drawings()로 묶인 도형 영역 중 표·글상자·그림 상자를 뺀 것"""
	page_area = page.rect.get_area()
	tables = None
	rects = []
	for rect in page.cluster_drawings():
		if not _is_big(rect, min_pt):
			continue
		if rect.get_area() >= page_area * FULL_PAGE_RATIO:
			continue
		# 래스터 차트를 감싼 상자 (같은 차트를 두 번 자르지 않게)
		if any((raster & rect).get_area() >= raster.get_area() * OVERLAP_RATIO for raster in rasters):
			continue
		# 표: 표 찾기는 오래 걸려서 큰 도형 묶음이 있는 쪽에서만 한 번 실행한다
		if tables is None:
			tables = [pymupdf.Rect(table.bbox) for table in page.find_tables().tables]
		if any((table & rect).get_area() >= rect.get_area() * OVERLAP_RATIO for table in tables):
			continue
		# 글상자·요약 쪽 테두리
		if _text_ratio(words, rect) >= TEXT_BOX_RATIO:
			continue
		rects.append(rect)
	return rects


def _near_text(words: list, rect: pymupdf.Rect, above: bool) -> str:
	"""rect 바로 위(above=True) 또는 아래 CAPTION_GAP 안, rect 가로 범위 안의 낱말을 읽는 순서로 이은 글"""
	picked = []
	for word in words:
		x0, y0, x1, y1 = word[:4]
		# 낱말이 rect 가로 범위와 겹쳐야 한다 (나란히 놓인 두 차트의 캡션을 섞지 않게)
		# 가운데 기준으로 하면 차트보다 넓은 캡션의 '<'가 빠진다 (HRI 트럼프 노믹스 26·46·48쪽)
		if x1 <= rect.x0 or x0 >= rect.x1:
			continue
		if above and rect.y0 - CAPTION_GAP <= y1 <= rect.y0 + 2:
			picked.append(word)
		elif not above and rect.y1 - 2 <= y0 <= rect.y1 + CAPTION_GAP:
			picked.append(word)
	# pymupdf가 읽은 순서(블록 번호, 줄 번호, 낱말 번호)대로 잇는다
	# ('<'·'>'는 한글과 글꼴이 달라 y 좌표가 조금씩 어긋나므로 좌표로 정렬하면 순서가 섞인다)
	# 두 줄로 나뉜 캡션 '< 한국의 민간신용(GDP대비)' + '비율 추이 >'도 이렇게 이어진다
	picked.sort(key=lambda word: (word[5], word[6], word[7]))
	# 같은 줄(블록 번호, 줄 번호)의 낱말은 공백으로, 줄과 줄은 '\n'으로 잇는다
	# ('[그림 n] 제목' 아래 '자료 :' 줄이 캡션에 붙지 않게)
	lines = {}
	for word in picked:
		lines.setdefault((word[5], word[6]), []).append(word[4])
	return "\n".join(" ".join(line) for line in lines.values())


def _find_caption(words: list, rect: pymupdf.Rect) -> str | None:
	"""영역 위에서 먼저, 없으면 아래에서 캡션을 찾는다. 여러 개면 모두 이어 붙인다 (그림 하나에 차트 두 개). 없으면 None"""
	for above in (True, False):
		captions = CAPTION_PATTERN.findall(_near_text(words, rect, above))
		if captions:
			return " ".join(" ".join(caption.split()) for caption in captions)
	return None


def _save_png(page: pymupdf.Page, rect: pymupdf.Rect, dpi: int) -> str:
	"""rect 부분을 dpi로 그려 CHART_DIR에 uuid 파일 이름 PNG로 저장하고 경로를 돌려준다"""
	CHART_DIR.mkdir(parents=True, exist_ok=True)
	path = CHART_DIR / f"{uuid.uuid4()}.png"
	page.get_pixmap(clip=rect, dpi=dpi).save(str(path))
	return path.as_posix()


def extract_charts(file_path: str) -> list[ChartImage]:
	"""
	PDF에서 차트 영역을 찾아 PNG로 잘라 내고 ChartImage 목록을 돌려준다.

	1) 쪽마다 차트 영역을 찾는다
	   ① 래스터 차트: page.get_images(full=True) + page.get_image_rects(xref)로 위치
	   ② 벡터 차트: page.cluster_drawings()로 묶인 도형 영역
	2) 가로·세로 MIN_CHART_PT(120pt) 미만, 같은 xref가 3쪽 이상 나오는 그림(로고)은 뺀다.
	   쪽 전체를 덮는 영역(표지 배경)도 뺀다.
	   벡터 묶음은 래스터 차트를 감싼 상자, 표(find_tables), 글상자(글자 넓이 20% 이상)도 뺀다.
	3) 영역 위·아래에서 캡션('[그림 n]', '<그림 n>', '[차트 n]', HRI '< 제목 >')을 찾는다. 없으면 None.
	4) page.get_pixmap(clip=rect, dpi=CHART_DPI)로 잘라 data/charts/raw/{uuid}.png에 저장한다.
	5) page는 pymupdf 쪽 인덱스+1 (1부터). 쪽 순서, 쪽 안에서는 위 → 아래, 왼 → 오른쪽 순서로 돌려준다.

	차트가 없으면 빈 목록. CHART_DPI·MIN_CHART_PT는 Settings(load_settings)에서 읽는다.
	"""
	settings = load_settings()
	min_pt = settings.min_chart_pt
	dpi = settings.chart_dpi

	charts = []
	with pymupdf.open(file_path) as doc:
		logos = _logo_xrefs(doc)
		for page in doc:
			words = page.get_text("words")
			rasters = _raster_rects(page, logos, min_pt)
			vectors = _vector_rects(page, rasters, words, min_pt)
			for rect in sorted(rasters + vectors, key=lambda rect: (rect.y0, rect.x0)):
				charts.append(ChartImage(
					page=page.number + 1,
					caption=_find_caption(words, rect),
					bbox=tuple(rect),
					image_path=_save_png(page, rect, dpi),
				))
	return charts


# ---------------------------------------------------------------------
# ocr_chart()에서 쓰는 값
# ---------------------------------------------------------------------
# 숫자 토큰: '3.5', '-1.0', '2024', '12000' (쉼표·공백을 정리한 뒤 이 모양이면 숫자로 본다)
NUMBER = re.compile(r"[-+]?\d+(\.\d+)?")

# 천 단위 쉼표: '12,000', '600,000' 처럼 쉼표 뒤가 꼭 3자리일 때만 쉼표를 지운다
# '3,5'·'52,6'은 OCR이 소수점을 쉼표로 잘못 읽은 것이라(HRI 5편 차트 107개 중 18토큰) 지우면 35·526이 되므로 숫자로 보지 않는다
THOUSANDS = re.compile(r"[-+]?\d{1,3}(,\d{3})+(\.\d+)?")

# 점·쉼표 옆 공백: '61. 7' → '61.7' (HRI 5편 차트 OCR 1,831토큰 중 1개. 소수점 뒤에 공백을 넣어 읽은 경우)
# '1065 1070'처럼 숫자와 숫자 사이 공백은 두 값이 붙어 읽힌 것이라 지우지 않는다 (→ 숫자로 보지 않음)
SPACE_NEAR_MARK = re.compile(r"\s*([.,])\s*")

# EasyOCR Reader (처음 get_reader()를 부를 때 한 번 만들고 계속 재사용한다)
_reader = None


def get_reader():
	"""EasyOCR Reader를 한 번만 만들어 돌려준다 (두 번째부터는 만들어 둔 것을 그대로 돌려줌).

	- Reader를 만들 때 글자 인식 모델을 불러오므로 몇 초씩 걸린다 → 차트마다 새로 만들지 않는다
	- 처음 실행할 때는 모델 파일을 인터넷에서 내려받는다(약 100MB, ~/.EasyOCR/model/)
	  → 시연 전에 한 번 미리 실행해 둔다:
	    poetry run python -c "from hri.loader.chart_loader import get_reader; get_reader()"
	- 언어는 .env의 OCR_LANGS('ko,en')를 쉼표로 나눠서 쓴다. GPU는 쓰지 않는다(gpu=False)
	- 여러 스레드가 함께 쓰지 않도록 process_charts_node가 ocr_chart()를 순서대로 부른다
	"""
	global _reader
	if _reader is None:
		# easyocr는 torch를 함께 불러와 느리므로, 이 모듈을 import할 때가 아니라 Reader를 만들 때 불러온다
		import easyocr

		settings = load_settings()
		langs = [lang.strip() for lang in settings.ocr_langs.split(",") if lang.strip()]
		_reader = easyocr.Reader(langs, gpu=False)
	return _reader


def _clean_number(text: str) -> str | None:
	"""숫자 토큰이면 정리한 글자('12,000' → '12000')를, 숫자가 아니면 None을 돌려준다.

	1. 전각 기호를 반각으로: '１２．５' → '12.5' (unicodedata NFKC)
	2. 앞뒤 공백, 점·쉼표 옆 공백을 지운다: '61. 7' → '61.7'
	3. 천 단위 쉼표를 지운다: '12,000' → '12000' (쉼표 뒤가 3자리일 때만)
	4. 남은 글자가 NUMBER 모양이면 숫자
	"""
	cleaned = unicodedata.normalize("NFKC", text).strip()
	cleaned = SPACE_NEAR_MARK.sub(r"\1", cleaned)
	if THOUSANDS.fullmatch(cleaned):
		cleaned = cleaned.replace(",", "")
	if NUMBER.fullmatch(cleaned):
		return cleaned
	return None


def _sort_tokens(tokens: list[OcrToken]) -> list[OcrToken]:
	"""토큰을 위→아래, 왼→오른쪽 순으로 정렬한다.

	위쪽 y만으로 정렬하면 같은 줄인데 1~2px 높이가 다른 글자의 순서가 뒤바뀐다
	(예: x축 '2024'(y=414)보다 '2026(E)'(y=412)가 먼저 나옴. HRI 5편 차트 107개 중 87개에서 생김).
	그래서 먼저 줄로 묶고, 줄 안에서 왼→오른쪽으로 정렬한다.
	- 줄 묶기: 위에서부터 보면서, 토큰의 세로 가운데가 지금 줄 첫 토큰의 위·아래 범위 안이면 같은 줄
	"""
	lines: list[list[OcrToken]] = []
	for token in sorted(tokens, key=lambda t: (t.bbox[1], t.bbox[0])):
		middle = (token.bbox[1] + token.bbox[3]) / 2
		if lines:
			first = lines[-1][0]
			if first.bbox[1] <= middle <= first.bbox[3]:
				lines[-1].append(token)
				continue
		lines.append([token])

	result: list[OcrToken] = []
	for line in lines:
		result.extend(sorted(line, key=lambda t: t.bbox[0]))
	return result


def ocr_chart(image_path: str) -> list[OcrToken]:
	"""차트 그림(PNG)에서 글자를 읽어 OcrToken 목록으로 돌려준다 (축 눈금·범례·데이터 레이블 후보).

	1. get_reader()로 만들어 둔 EasyOCR Reader를 가져온다
	2. reader.readtext(image_path, detail=1) → [(네 꼭짓점, 글자, 신뢰도), ...]
	3. 신뢰도 < OCR_MIN_CONF(.env, 기본 0.4)인 글자는 버린다
	4. 네 꼭짓점 → (x0, y0, x1, y1) (x·y의 최소·최대)
	5. 숫자는 쉼표·공백·전각 기호를 정리하고 is_number=True (_clean_number)
	   숫자가 아닌 글자는 읽은 그대로 둔다
	6. 위→아래, 왼→오른쪽 순으로 정렬해 돌려준다 (_sort_tokens)

	OCR이 실패하면(그림 파일이 없거나 깨짐, 모델을 불러오지 못함 등) ChartExtractError를 낸다.
	process_charts_node가 잡아서 이 차트만 건너뛰고 warnings에 남긴다.
	"""
	settings = load_settings()
	min_conf = settings.ocr_min_conf

	try:
		reader = get_reader()
		results = reader.readtext(image_path, detail=1)
	except Exception as error:
		raise ChartExtractError(f"{image_path}: 차트 글자를 읽지 못함 ({error})") from error

	tokens: list[OcrToken] = []
	for points, text, conf in results:
		if conf < min_conf:
			continue

		xs = [float(point[0]) for point in points]
		ys = [float(point[1]) for point in points]
		bbox = (min(xs), min(ys), max(xs), max(ys))

		number = _clean_number(text)
		if number is not None:
			tokens.append(OcrToken(text=number, bbox=bbox, conf=float(conf), is_number=True))
		else:
			tokens.append(OcrToken(text=text, bbox=bbox, conf=float(conf), is_number=False))

	return _sort_tokens(tokens)


# ---------------------------------------------------------------------
# chart_to_table()·validate_chart()·chart_to_markdown()에서 쓰는 값
# ---------------------------------------------------------------------
# Vision 응답 최대 토큰 (명세서 chart_to_table 행: max_tokens=1500, get_chat_model 행: '긴 구조화 출력은 호출부에서 1500 지정')
VISION_MAX_TOKENS = 1500

# validate_chart: OCR에 실제로 있는 숫자 비율이 이보다 작으면 needs_review=True (명세서 validate_chart 행: match_ratio < 0.5)
MIN_MATCH_RATIO = 0.5

# chart_to_markdown: 표의 왼쪽 위 칸 (x축 라벨 열의 이름). HRI 보고서 표의 왼쪽 위 칸 '구 분'과 같은 뜻
X_LABEL_HEADER = "구분"

# Vision 모델에 보내는 지시문. {caption}에 캡션 줄, {tokens}에 OCR 글자 목록이 들어간다
# 규칙 1~3은 명세서 chart_to_table 행 그대로 (OCR 숫자 우선 · 근사값이면 needs_review · 범례에 없는 계열 금지)
# 규칙 3 둘째 문장: HRI 차트 중 범례 없는 계열 1개짜리(예: '3%대 성장…' 10쪽 왼쪽·11쪽 오른쪽)는 계열 이름을 캡션에서 가져와야 해서 넣음
# 규칙 4~6: ChartData·ChartSeries 필드 설명(x축 순서, 못 읽은 칸 None, 단위 없으면 None, confidence는 validate_chart가 채움)을 지시문으로 옮김
CHART_PROMPT = """그림은 경제 보고서 속 차트 1개입니다. 차트를 표 데이터로 복원하세요.
{caption}
[규칙]
1. 값은 아래 OCR 글자 중 숫자(데이터 레이블)를 우선 사용합니다.
2. 데이터 레이블이 없어 축 눈금을 보고 읽은 근사값이 하나라도 있으면 needs_review를 true로 합니다.
3. 범례에 없는 계열 이름은 만들지 않습니다. 범례가 없는 계열 1개짜리 차트는 캡션이나 그림 속 제목을 계열 이름으로 씁니다.
4. x_labels는 x축 라벨을 왼쪽부터 순서대로 적습니다 (원그래프는 조각 이름).
5. 계열마다 values는 x_labels와 같은 순서·같은 개수로 적고, 읽을 수 없는 값은 null로 둡니다. 숫자를 지어내지 않습니다.
6. unit은 그림에 적힌 단위(예: %, 억 달러)만 적고, 없으면 null입니다. confidence는 비워 둡니다.

[OCR 글자] 글자 (x0, y0, x1, y1) 픽셀 좌표, 위→아래·왼→오른쪽 순
{tokens}"""


def _format_tokens(tokens: list[OcrToken]) -> str:
	"""OCR 토큰 목록을 지시문에 넣을 글로 만든다. 한 줄에 '글자 (x0, y0, x1, y1)' 1개.

	좌표를 같이 보내는 이유: 같은 숫자가 여러 번 나올 때 어느 막대·점 옆 글자인지 그림과 맞춰 보게 하려고
	(명세서 chart_to_table 행: 'OCR 토큰 목록(텍스트·좌표)')
	"""
	if not tokens:
		return "(읽은 글자 없음)"
	lines = []
	for token in tokens:
		x0, y0, x1, y1 = token.bbox
		lines.append(f"{token.text} ({x0:.0f}, {y0:.0f}, {x1:.0f}, {y1:.0f})")
	return "\n".join(lines)


def chart_to_table(chart: ChartImage, tokens: list[OcrToken]) -> ChartData:
	"""[차트 구조화] 차트 그림과 OCR 글자를 Vision 모델에 보내 표 데이터(ChartData)로 복원한다.

	순서 (명세서 chart_to_table 행)
	1. VISION_MODEL은 Settings에서 읽는다 (load_settings().vision_model)
	   - .env에 'VISION_MODEL='처럼 빈 값이 있으면 os.getenv가 ''를 돌려주고, get_chat_model('')은 LLM_MODEL로 바뀐다
	     → 다른 모델로 몰래 호출되지 않도록 ChartExtractError를 낸다
	2. 차트 PNG를 base64 data URL('data:image/png;base64,...')로 바꾼다
	3. HumanMessage(content=[지시문+OCR 토큰 목록 글, image_url]) 1개를 만든다
	4. get_chat_model(VISION_MODEL, temperature=0, max_tokens=1500).with_structured_output(ChartData)로 호출한다
	5. 규칙(지시문 CHART_PROMPT): OCR 숫자 우선 · 눈금으로 읽은 근사값이 있으면 needs_review=True · 범례에 없는 계열은 만들지 않음

	실패하면 ChartExtractError (process_charts_node가 그 차트만 건너뛰고 warnings에 메시지를 남긴다)
	- 그림 파일을 읽지 못함 / Vision 호출·형식 변환 실패 / 응답에 x축 라벨이나 계열이 없음
	- 메시지는 'n쪽 차트: …' (fakes.py·test_ingest_nodes의 경고 문구와 같은 모양)
	confidence는 여기서 채우지 않는다. validate_chart()가 match_ratio로 채운다.
	"""
	# llm_client는 import할 때 .env를 읽으므로 함수 안에서 import한다 (ingest_graph 노드들과 같은 방식)
	from hri.llm.llm_client import get_chat_model

	# 1) 모델 이름
	vision_model = load_settings().vision_model
	if not vision_model:
		raise ChartExtractError(f"{chart.page}쪽 차트: VISION_MODEL이 비어 있습니다 (.env의 VISION_MODEL에 팀에서 정한 모델 이름을 넣어 주세요)")

	# 2) 그림 → base64 data URL
	try:
		with open(chart.image_path, "rb") as file:
			image_b64 = base64.b64encode(file.read()).decode("utf-8")
	except OSError as error:
		raise ChartExtractError(f"{chart.page}쪽 차트: 그림 파일을 읽지 못함 ({chart.image_path})") from error

	# 3) 글 + 그림을 담은 메시지 1개
	caption = f"캡션: {chart.caption}\n" if chart.caption else ""
	text = CHART_PROMPT.format(caption=caption, tokens=_format_tokens(tokens))
	message = HumanMessage(content=[
		{"type": "text", "text": text},
		{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
	])

	# 4) Vision 호출 → ChartData
	try:
		model = get_chat_model(
			model=vision_model,
			temperature=0,
			max_tokens=VISION_MAX_TOKENS,
		).with_structured_output(ChartData)
		data = model.invoke([message])
	except Exception as error:
		raise ChartExtractError(
			f"{chart.page}쪽 차트: Vision 응답을 ChartData로 바꾸지 못함 ({error})"
		) from error

	# 표로 만들 내용이 없으면 실패로 본다 (빈 표가 저장·검색되지 않게)
	if data is None or not data.x_labels or not data.series:
		raise ChartExtractError(f"{chart.page}쪽 차트: Vision 응답에 x축 라벨이나 계열이 없음")
	return data


def validate_chart(data: ChartData, tokens: list[OcrToken]) -> ChartData:
	"""[추출 결과 검수] Vision이 복원한 값을 OCR 글자와 맞춰 보고 confidence·needs_review를 채운 ChartData를 돌려준다.

	순서 (명세서 validate_chart 행)
	1. 계열마다 값 개수 = x축 라벨 개수인지 확인한다
	2. LLM이 낸 숫자(None 뺀 값) 중 OCR 숫자 토큰(is_number=True)에 실제로 있는 비율 = match_ratio → confidence
	   - 숫자 토큰 글자는 ocr_chart()가 NUMBER 모양('3.5', '-1.0', '12000')으로 정리해 두었으므로 float()로 바로 비교한다
	   - 값이 하나도 없으면 0.0
	3. needs_review = 이미 True(chart_to_table에서 근사값) 또는 개수가 다름 또는 match_ratio < 0.5
	   - True여도 버리지 않고 저장한다. 답변 출처에 '(추정값)'이 붙는다 (source_formatter·rag_prompt)
	받은 data는 바꾸지 않고 model_copy로 새 ChartData를 만든다.
	"""
	counts_ok = all(len(series.values) == len(data.x_labels) for series in data.series)

	values = [value for series in data.series for value in series.values if value is not None]
	ocr_numbers = {float(token.text) for token in tokens if token.is_number}
	matched = sum(1 for value in values if value in ocr_numbers)
	match_ratio = matched / len(values) if values else 0.0

	needs_review = data.needs_review or not counts_ok or match_ratio < MIN_MATCH_RATIO
	return data.model_copy(update={"confidence": match_ratio, "needs_review": needs_review})


def _format_value(value: float | None) -> str:
	"""표 칸에 넣을 글. 못 읽은 값(None)은 빈칸 '', 나머지는 str() 그대로 (3.0 → '3.0', 50.8 → '50.8').

	HRI 차트 데이터 레이블은 거의 모두 소수 첫째 자리까지 적혀 있어('3.0'·'0.0'·'50.8') str() 결과와 같다.
	"""
	if value is None:
		return ""
	return str(value)


def chart_to_markdown(data: ChartData, chart: ChartImage) -> Table:
	"""[표 형식 변환] ChartData를 마크다운 표로 바꿔 Table(source_type='chart')로 돌려준다.

	순서 (명세서 chart_to_markdown 행)
	1. rows: 머리글 = [구분, 계열 이름…], 본문 = x축 라벨마다 [라벨, 계열별 값…] (x축 라벨이 행, 계열이 열)
	   - 단위가 있으면 왼쪽 위 칸에 '구분 (단위: %)'로 붙인다 (Table에 단위 칸이 없어 표 안에 남긴다)
	   - 못 읽은 값(None)과 값 개수가 라벨보다 적은 계열의 모자란 칸은 빈칸, 많으면 넘치는 값은 버린다 (개수 불일치는 validate_chart가 needs_review로 표시)
	2. markdown = table_to_markdown(rows) (표 노드와 같은 함수 재사용)
	3. Table(source_type='chart', image_path, confidence, needs_review)
	   - caption: 차트 캡션(extract_charts가 찾은 '< 제목 >'·'[그림 n]'), 없으면 차트 안 제목(data.title)
	   - 캡션·추정값 여부는 마크다운 안에 다시 쓰지 않는다: 답변·출처·보고서 본문을 만드는 곳(rag_prompt.format_docs,
	     source_formatter, report_service._format_table)이 Table.caption·needs_review로 이미 '[… / 캡션] (추정값)'을 붙인다
	이후 표와 같은 길(summarize_tables → split → persist)로 저장·검색된다.
	"""
	corner = f"{X_LABEL_HEADER} (단위: {data.unit})" if data.unit else X_LABEL_HEADER
	rows: list[list[str]] = [[corner] + [series.name for series in data.series]]
	for index, label in enumerate(data.x_labels):
		row = [label]
		for series in data.series:
			value = series.values[index] if index < len(series.values) else None
			row.append(_format_value(value))
		rows.append(row)

	return Table(
		page=chart.page,
		caption=chart.caption or data.title,
		rows=rows,
		markdown=table_to_markdown(rows),
		source_type="chart",
		image_path=chart.image_path,
		confidence=data.confidence,
		needs_review=data.needs_review,
	)
