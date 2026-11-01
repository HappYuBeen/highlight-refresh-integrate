"""load_pdf()·clean_text()·find_body_start()·extract_report_meta()가 명세대로 동작하는지 확인하는 테스트 (네트워크·DB·.env 없이 실행).

load_pdf() 테스트용 PDF는 pymupdf로 tmp_path(pytest가 주는 임시 폴더)에 만든다.
clean_text()·find_body_start()는 Page 목록을 직접 만들어 넣는다 (HRI 보고서 쪽 모양을 줄여서 흉내 냄).
"""

from datetime import date

import pymupdf

from hri.domain.schemas import Page
from hri.loader.pdf_loader import clean_text, extract_report_meta, find_body_start, load_pdf


# 쪽마다 texts[i]를 써 넣은 PDF를 만든다. 빈 글자("")면 아무것도 안 쓴 빈 쪽 (스캔본 흉내)
def make_pdf(path, texts, title=None):
	doc = pymupdf.open()
	# title: PDF 메타데이터 제목(/Title). None이면 비워 둔다 (HRI 37편처럼)
	if title is not None:
		doc.set_metadata({"title": title})
	for text in texts:
		page = doc.new_page(width=595, height=842)
		if text:
			# fontname="korea": pymupdf에 들어 있는 한글 글꼴
			page.insert_text((72, 72), text, fontname="korea")
	doc.save(str(path))
	doc.close()
	return str(path)


# HRI 본문 쪽 모양: 1줄 보고서 제목, 2줄 쪽 번호, 그 아래 본문
def body_page(page_no, printed_no, body):
	return Page(page=page_no, text=f"물가 안정, 이제는 장기전으로\n{printed_no}\n{body}")


# ---------------------------------------------------------------------
# load_pdf()
# ---------------------------------------------------------------------
# [확인 1] 쪽마다 글자를 뽑고, 쪽 번호는 1부터
def test_load_pdf_page_numbers(tmp_path):
	pages = load_pdf(make_pdf(tmp_path / "a.pdf", ["첫 쪽", "둘째 쪽", "셋째 쪽"]))
	assert [p.page for p in pages] == [1, 2, 3]
	assert "첫 쪽" in pages[0].text and "셋째 쪽" in pages[2].text
	assert all(isinstance(p, Page) for p in pages)


# [확인 2] 글자가 없는 쪽만 is_scanned=True (막지 않고 표시만)
def test_load_pdf_scanned_page(tmp_path):
	pages = load_pdf(make_pdf(tmp_path / "b.pdf", ["글자 있음", "", "글자 있음"]))
	assert [p.is_scanned for p in pages] == [False, True, False]
	assert pages[1].text.strip() == ""


# [확인 3] 다 읽은 뒤 파일이 닫힌다 (닫히지 않으면 Windows에서 삭제가 실패)
def test_load_pdf_closes_file(tmp_path):
	path = tmp_path / "c.pdf"
	load_pdf(make_pdf(path, ["글자"]))
	path.unlink()
	assert not path.exists()


# ---------------------------------------------------------------------
# clean_text()
# ---------------------------------------------------------------------
# [확인 4] 여러 쪽에 반복되는 머리말(보고서 제목)과 쪽 번호 줄을 지우고 본문은 남긴다
def test_clean_text_removes_header_and_page_no():
	pages = [body_page(i, i, f"{i}번째 쪽 본문") for i in range(1, 5)]
	cleaned = clean_text(pages)
	assert [p.text for p in cleaned] == ["1번째 쪽 본문", "2번째 쪽 본문", "3번째 쪽 본문", "4번째 쪽 본문"]


# [확인 5] 쪽 번호(page)·is_scanned는 그대로, 들어온 pages는 바꾸지 않는다
def test_clean_text_keeps_page_and_input():
	pages = [body_page(i, i, "본문") for i in range(1, 4)] + [Page(page=4, text="", is_scanned=True)]
	before = [p.text for p in pages]
	cleaned = clean_text(pages)
	assert [p.page for p in cleaned] == [1, 2, 3, 4]
	assert [p.is_scanned for p in cleaned] == [False, False, False, True]
	assert [p.text for p in pages] == before


# [확인 6] 본문 가운데에 반복되는 줄(표 출처)은 지우지 않는다 (위·아래 2줄만 머리말·꼬리말 후보)
def test_clean_text_keeps_repeated_middle_line():
	body = "본문 첫 줄\n자료 : 한국은행.\n본문 둘째 줄\n본문 셋째 줄\n본문 넷째 줄"
	cleaned = clean_text([body_page(i, i, body) for i in range(1, 5)])
	assert all("자료 : 한국은행." in p.text for p in cleaned)


# [확인 7] 절반 미만의 쪽에만 있는 줄은 지우지 않는다 (10쪽 중 2쪽의 맨 아래 출처 줄)
def test_clean_text_ratio():
	pages = [body_page(i, i, f"본문 {i}\n다음 줄") for i in range(1, 11)]
	pages[3] = body_page(4, 4, "본문 4\n자료 : 한국은행.")
	pages[7] = body_page(8, 8, "본문 8\n자료 : 한국은행.")
	cleaned = clean_text(pages)
	assert cleaned[3].text.endswith("자료 : 한국은행.")
	assert cleaned[7].text.endswith("자료 : 한국은행.")


# [확인 8] 아래쪽 숫자 줄(그래프 눈금)은 쪽 번호가 위에 있는 보고서에서 지우지 않는다
def test_clean_text_keeps_bottom_numbers():
	pages = [body_page(i, i, "본문\n다음 줄") for i in range(1, 5)]
	pages[1] = body_page(2, 2, "본문\n< 그래프 >\n18\n12")
	cleaned = clean_text(pages)
	assert cleaned[1].text == "본문\n< 그래프 >\n18\n12"


# [확인 9] 로마 숫자 쪽 번호(i, ii)와 아래쪽 쪽 번호('- 3 -')도 지운다
def test_clean_text_roman_and_footer_page_no():
	pages = [Page(page=i, text=f"본문 {i}\n- {i} -") for i in range(1, 4)]
	pages += [Page(page=4, text="EXECUTIVE SUMMARY\nii\n요약 본문")]
	pages += [Page(page=5, text="EXECUTIVE SUMMARY\niii\n요약 본문2")]
	cleaned = clean_text(pages)
	assert [p.text for p in cleaned[:3]] == ["본문 1", "본문 2", "본문 3"]


# [확인 10] 연속 공백은 한 칸, 빈 줄은 지우고, 깨진 글자(수식, U+E000~U+F8FF)도 지운다
def test_clean_text_whitespace_and_broken_chars():
	page = Page(page=1, text="  국제유가가   급등\t하면서 \n\n\n물가\u3000상승\n잠재\ue006\ue003\ue00f갭률\n\ue0f8\ue0f6\n")
	cleaned = clean_text([page])
	assert cleaned[0].text == "국제유가가 급등 하면서\n물가 상승\n잠재갭률"


# [확인 11] 1쪽짜리는 반복을 판단할 수 없으므로 줄을 지우지 않는다
def test_clean_text_single_page():
	cleaned = clean_text([body_page(1, 1, "본문")])
	assert cleaned[0].text == "물가 안정, 이제는 장기전으로\n1\n본문"


# ---------------------------------------------------------------------
# find_body_start()
# ---------------------------------------------------------------------
# HRI 앞부분 모양: 표지 → 목차('1. 개요······ 1' 포함) → 요약 → 본문
HRI_PAGES = [
	Page(page=1, text="물가 안정, 이제는 장기전으로\n- 2026년 상반기 물가 평가 및 하반기 전망\n26-14(통권 1015호) 2026.07.31"),
	Page(page=2, text="목 차\n■ 물가 안정, 이제는 장기전으로\nExecutive Summary ·········· ⅰ\n1. 개요······································ 1\n2. 품목별 물가 동향······················ 2"),
	Page(page=3, text="EXECUTIVE SUMMARY\nExecutive Summary\n□물가 안정, 이제는 장기전으로\n￭개 요\n요약 내용"),
	Page(page=4, text="1. 개요\n¡ 본문 내용"),
]


# [확인 12] 목차 쪽의 '1. 개요······ 1'을 본문으로 고르지 않고, 목차 다음 요약 쪽부터 남긴다
def test_find_body_start_skips_toc():
	body = find_body_start(HRI_PAGES)
	assert [p.page for p in body] == [3, 4]
	assert body[0].text.startswith("EXECUTIVE SUMMARY")


# [확인 13] 목차 쪽이 없으면 첫 쪽부터 찾고, 그 쪽은 패턴 줄 앞을 잘라 낸다
def test_find_body_start_without_toc_cuts_in_page():
	pages = [
		Page(page=1, text="보고서 제목\n- 부제"),
		Page(page=2, text="발간 2026.07.31\nⅠ. 최근 경제 동향\n본문 내용"),
		Page(page=3, text="Ⅱ. 전망\n본문"),
	]
	body = find_body_start(pages)
	assert [p.page for p in body] == [2, 3]
	assert body[0].text == "Ⅰ. 최근 경제 동향\n본문 내용"


# [확인 14] '1.1%'처럼 점 뒤가 숫자인 줄은 장 제목으로 보지 않는다
def test_find_body_start_ignores_numbers():
	pages = [
		Page(page=1, text="목차\n1. 개요 ········ 1"),
		Page(page=2, text="1.1%\n2026년 성장률"),
		Page(page=3, text="1. 개요\n본문"),
	]
	body = find_body_start(pages)
	assert [p.page for p in body] == [3]


# [확인 15] 본문 시작 패턴을 못 찾으면 pages를 그대로 돌려준다 (안전장치)
def test_find_body_start_not_found():
	pages = [Page(page=1, text="목차\n가. 첫째"), Page(page=2, text="가. 첫째\n내용")]
	assert find_body_start(pages) == pages
	assert find_body_start([]) == []


# [확인 16] clean_text() → find_body_start() 순서로 이어서 써도 쪽 번호가 유지된다
def test_clean_then_find(tmp_path):
	texts = ["표지 제목\n- 부제", "목 차\n1. 개요············ 1"]
	texts += [f"보고서 제목\n{n}\n" + ("1. 개요\n본문" if n == 1 else f"본문 {n}") for n in range(1, 4)]
	pages = load_pdf(make_pdf(tmp_path / "d.pdf", texts))
	body = find_body_start(clean_text(pages))
	assert [p.page for p in body] == [3, 4, 5]
	assert body[0].text == "1. 개요\n본문"


# [확인 17] 목차 줄에 점선이 없어도('1. 개요 1') 목차 쪽 다음부터 찾으므로 목차를 본문으로 고르지 않는다
def test_find_body_start_toc_without_leader():
	pages = [
		Page(page=1, text="표지"),
		Page(page=2, text="목 차\n1. 개요 1\n2. 동향 3"),
		Page(page=3, text="1. 개요\n본문"),
	]
	assert [p.page for p in find_body_start(pages)] == [3]


# [확인 18] '목차' 제목이 없는 목차 쪽도 점선 뒤 쪽 번호가 붙은 줄은 본문으로 고르지 않는다
def test_find_body_start_leader_without_toc_title():
	pages = [
		Page(page=1, text="CONTENT LIST\n1. 개요··········· 1\n2. 동향··········· 3"),
		Page(page=2, text="1. 개요\n본문"),
	]
	assert [p.page for p in find_body_start(pages)] == [2]


# ---------------------------------------------------------------------
# extract_report_meta()
# ---------------------------------------------------------------------
# [확인 19] HRI 표지: 메타데이터 제목이 비어 있으면 첫 쪽에서 찾는다.
#           발간번호 줄을 건너뛰고 두 줄로 나뉜 제목을 부제('- …') 앞까지 붙이며, 날짜·발행기관(발간번호 줄)도 읽는다.
#           정제 전 pages를 넘긴다 (clean_text()는 본문 머리말과 같은 표지 제목 줄을 지움)
def test_extract_report_meta_hri_cover(tmp_path):
	cover = "26-07(통권 1008호) 2026.04.30\n미-이란 전쟁 이후의 경제 전략을 생각하며\n(2026년 수정 경제 전망)\n- 2026년 경제성장률 2.7%"
	body = "미-이란 전쟁 이후의 경제 전략을 생각하며\n1\n1. 개요\n본문"
	path = make_pdf(tmp_path / "upload.pdf", [cover, body, body.replace("\n1\n", "\n2\n")])

	meta = extract_report_meta(path, load_pdf(path))

	assert meta == {
		"title": "미-이란 전쟁 이후의 경제 전략을 생각하며 (2026년 수정 경제 전망)",
		"publisher": "현대경제연구원",
		"published_date": date(2026, 4, 30),
	}


# [확인 20] HRI가 아닌 보고서: 메타데이터 제목이 있으면 그것을 쓰고, 발행기관·발행일을 못 찾으면 None.
#           메타데이터 제목이 파일 이름('….png')이고 첫 쪽에 글자가 없으면 파일 이름(확장자 뺀 이름)을 제목으로 쓴다
def test_extract_report_meta_other_publisher(tmp_path):
	path = make_pdf(tmp_path / "kdi.pdf", ["경제동향 2026년 10월호\n본문"], title="KDI 경제동향")
	assert extract_report_meta(path, load_pdf(path)) == {"title": "KDI 경제동향", "publisher": None, "published_date": None}

	path = make_pdf(tmp_path / "스캔 보고서.pdf", [""], title="684314165.png (4468×4144)")
	assert extract_report_meta(path, load_pdf(path)) == {"title": "스캔 보고서", "publisher": None, "published_date": None}
