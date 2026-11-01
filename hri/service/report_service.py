"""보고서 목록·본문 · search_reports, owner_filter, get_report_text (장유빈)"""
from hri.dao.report_dao import select_report_tables, select_reports, select_sections
from hri.domain.schemas.report import Report, Section, Table


def owner_filter(user) -> str | None:
	"""목록을 걸러 볼 user_id를 정한다. (state['user'] 또는 User 객체를 받는다)

	관리자(admin)는 전체를 보므로 None, 일반 사용자는 본인 아이디를 돌려준다.
	로그인 전(user가 None)이면 어떤 보고서와도 맞지 않는 ''를 돌려줘 아무것도 보이지 않게 한다.
	"""
	if user is None:
		return ""
	if isinstance(user, dict):
		role, user_id = user.get("role"), user.get("user_id")
	else:
		role, user_id = getattr(user, "role", None), getattr(user, "user_id", None)
	if role == "admin":
		return None
	return user_id or ""


def search_reports(keyword: str = "", limit: int | None = None, user_id: str | None = None) -> list[Report]:
	"""제목·발행기관 키워드로 보고서를 검색해 발행일 최신순으로 돌려준다.

	keyword가 ''이면 전체를, limit이 있으면 상위 limit개만 돌려준다.
	user_id가 있으면 그 사용자가 올린 보고서만, None이면 모든 보고서를 돌려준다.
	"""
	reports = select_reports(keyword.strip(), user_id)
	return reports[:limit] if limit is not None else reports


# =====================================================================
# [장유빈] 보고서 본문 · get_report_text (WBS 3.5.9)
# 업로드 때 DB에 저장해 둔 섹션 원문과 표·차트 마크다운을 이어 붙여 본문 한 덩어리로 만든다
# =====================================================================
def _format_section(section: Section) -> str:
	"""섹션 한 개를 '## 제목 (p.시작-끝)' 줄과 원문으로 만든다."""
	return f"## {section.title} (p.{section.start_page}-{section.end_page})\n{section.text}"


def _format_table(table: Table) -> str:
	"""표·차트 한 개를 '[표 p.12 / 캡션]' 줄과 마크다운으로 만든다. needs_review면 줄 끝에 '(추정)'을 붙인다."""
	kind = "차트" if table.source_type == "chart" else "표"
	header = f"[{kind} p.{table.page}"
	if table.caption:
		header += f" / {table.caption}"
	header += "]"
	if table.needs_review:
		header += " (추정)"
	return f"{header}\n{table.markdown}"


def get_report_text(report_id: int) -> str:
	"""보고서 본문을 섹션 원문 + 표·차트 마크다운으로 이어 붙여 돌려준다.

	섹션은 시작 쪽 순으로 잇고, 표·차트는 시작 쪽이 표의 쪽 이하인 섹션 중 가장 뒤 섹션 바로 아래에 둔다
	(첫 섹션보다 앞 쪽이면 맨 앞). 블록 사이는 빈 줄로 나누고, 섹션도 표도 없으면 빈 문자열이다.
	DB에 저장된 값만 쓰고 원본 PDF를 다시 읽지 않는다. extract_indicators()가 이 본문을 읽는다.
	"""
	# 섹션은 시작 쪽 순으로 (같으면 section_no 순), 표·차트는 DAO가 쪽 순으로 준다
	sections = sorted(select_sections(report_id), key=lambda s: (s.start_page, s.section_no))
	tables = select_report_tables(report_id)

	# 표·차트가 놓일 자리를 정한다: 맨 앞(front) 또는 섹션 번호(index) 바로 아래
	front: list[Table] = []
	under: dict[int, list[Table]] = {i: [] for i in range(len(sections))}
	for table in tables:
		index = -1
		for i, section in enumerate(sections):
			if section.start_page <= table.page:
				index = i
		if index == -1:
			front.append(table)
		else:
			under[index].append(table)

	# 맨 앞 표 → (섹션 → 그 섹션 아래 표) 순서로 잇는다
	blocks = [_format_table(t) for t in front]
	for i, section in enumerate(sections):
		blocks.append(_format_section(section))
		blocks.extend(_format_table(t) for t in under[i])
	return "\n\n".join(blocks)