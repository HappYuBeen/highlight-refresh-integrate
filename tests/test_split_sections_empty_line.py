from hri.splitter.text_splitter import split_sections
from hri.domain.schemas import Page

def test_첫_제목_전_빈줄로_시작해도_start_page가_1이상():
    pages = [Page(page=1, text="\n\n서론 문장입니다\n1. 개요\n내용")]
    sections = split_sections(pages)
    assert sections[0].title == "본문"
    assert sections[0].start_page == 1