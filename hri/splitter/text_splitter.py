"""청크 분할 · split_sections, split_text (김희진)"""
import re
from langchain_text_splitters import RecursiveCharacterTextSplitter
from hri.domain.schemas import Page, Section

# 목차 번호 + 제목 한 줄. 예) "Ⅰ. 최근 국내외 경제 동향", "1. 개요", "가. 수요 측면"
#   - 번호: 로마 숫자(Ⅰ~Ⅻ) / 1~2자리 숫자 / 가~하 한 글자
#   - 점 뒤에 공백이 있어야 함 ("2.0% 증가" 같은 숫자는 제외)
HEADING_RE = re.compile(r"^\s*(?:[Ⅰ-Ⅻ]+|\d{1,2}|[가-하])\.\s+(?P<title>\S.*?)\s*$")
HEADING_MAX_LEN = 40                                  # 이보다 긴 줄은 제목이 아니라 본문 문장으로 봄
SENTENCE_END_RE = re.compile(r"(?:다|음|함|임|됨)\.?$")  # '~했다.', '~하였음'으로 끝나면 본문
TOC_LEADER_RE = re.compile(r"(?:\.{3,}|…|·{3,})\s*\d*$")  # 목차의 '개요 ........ 3' 줄
NO_HEADING_TITLE = "본문"                              # 제목을 못 찾은 앞부분·전체에 붙이는 이름
# 문단 → 줄 → 문장 → 어절 → 글자 순으로 끊는다
SEPARATORS = ["\n\n", "\n", r"(?<=[.?!])\s+", " ", ""]

def split_sections(pages: list[Page]) -> list[Section]:
	"""
	목차 패턴(Ⅰ./1./가. + 제목) 줄을 기준으로 본문을 섹션으로 나눈다.

	- section_no는 0부터. 제목 줄도 섹션 text 첫 줄에 넣는다 (답변 근거에 제목이 보이도록).
	- 첫 제목 앞에 글이 있으면 '본문' 섹션으로, 제목이 하나도 없으면 전체를 '본문' 섹션 1개로 만든다.
	- 'Ⅰ. 큰 제목' 바로 아래에 '1. 작은 제목'이 오면(사이에 본문 없음) 제목을 'Ⅰ. … > 1. …'로 합친다.
	- 글이 없는 쪽(스캔본·빈 쪽)은 건너뛴다. report_id는 None (persist 노드에서 채움).
	"""
	sections: list[Section] = []
	title: str | None = None       # 지금 모으는 섹션의 제목 (None = 첫 제목 전)
	lines: list[str] = []          # 지금 섹션의 본문 줄 (제목 줄 제외)
	start_page = end_page = 0

	def close() -> None:
		"""모은 줄로 Section을 하나 만든다. 본문이 없으면 만들지 않는다."""
		body = "\n".join(lines).strip()
		if not body:
			return
		name = title or NO_HEADING_TITLE
		sections.append(Section(
			section_no=len(sections),
			title=name[:255],
			start_page=start_page,
			end_page=end_page,
			text=f"{name}\n{body}" if title else body,
		))

	for page in pages:
		for line in page.text.splitlines():
			heading = _heading_title(line)
			if heading is None:
				if not line.strip():
					if lines:
						lines.append("")
					continue
				if not lines and not title:
					start_page = page.page
				lines.append(line.rstrip())
				end_page = page.page
				continue

			# 새 제목: 앞 섹션 마무리. 본문 없이 제목만 있었다면 제목을 이어 붙인다
			if title and not "\n".join(lines).strip():
				heading = f"{title} > {heading}"
			else:
				close()
			title, lines = heading, []
			start_page = end_page = page.page

	close()
	return sections


def _heading_title(line: str) -> str | None:
	"""목차 제목 줄이면 '번호. 제목'을, 아니면 None을 돌려준다."""
	text = line.strip()
	if len(text) > HEADING_MAX_LEN or TOC_LEADER_RE.search(text):
		return None
	match = HEADING_RE.match(text)
	if not match or SENTENCE_END_RE.search(match.group("title")):
		return None
	return re.sub(r"\s+", " ", text)


def split_text(text: str, chunk_size: int = 500, chunk_overlap: int = 50) -> list[str]:
	"""
	글을 chunk_size 글자 이하 조각으로 나누고, 이웃 조각끼리 chunk_overlap 글자만큼 겹치게 한다.

	문단(빈 줄) → 줄 → 문장 → 어절(띄어쓰기) → 글자 순으로, 큰 단위부터 끊을 수 있으면 끊는다.
	(CHUNK_SIZE=500, CHUNK_OVERLAP=50에서 시작해 조각 수·표 깨짐을 보며 조정)
	chunk_overlap이 chunk_size 이상이면 ValueError. 빈 글이면 [].
	"""
	if chunk_size <= 0:
		raise ValueError(f"chunk_size는 1 이상이어야 합니다: {chunk_size}")
	if not 0 <= chunk_overlap < chunk_size:
		raise ValueError(f"chunk_overlap({chunk_overlap})은 0 이상, chunk_size({chunk_size}) 미만이어야 합니다")
	if not text.strip():
		return []

	splitter = RecursiveCharacterTextSplitter(
		chunk_size=chunk_size,
		chunk_overlap=chunk_overlap,
		separators=SEPARATORS,
		is_separator_regex=True,
		keep_separator="end",      # 마침표는 앞 문장 끝에 남긴다
	)
	return [piece.strip() for piece in splitter.split_text(text) if piece.strip()]
