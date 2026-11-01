# 프로젝트 구조 (초안)

> 상태: **초안 v0.3.1** · 팀 리뷰 후 확정
> 기준 문서: `01. 프로젝트기획서_Insight Bridge_v4.docx`, `기능명세서_경제분석보고서_AI요약서비스_v4.xlsx`, `경제분석보고서_AI요약서비스_WBS_v4.xlsx` (Google Drive · 현대 1차 4팀_내부자료)
> v0.3 변경: 기능명세서 v4 기준으로 다시 작성. 역할별 6개 그룹(`app/presentation` 등)을 없애고 `hri/` 아래에 명세서 패키지를 그대로 둠. LangGraph(`graph/`) 추가, MySQL·Chroma는 서버에 연결만 함
> v0.3.1 변경: 담당자를 WBS v4 작업 행 기준으로 변경, `.env.example`을 명세서 `load_settings()` 전체 항목으로 확장

## 0. 확정된 원칙

| 항목 | 결정 |
|---|---|
| 최상위 배치 | `hri/` 단일 패키지. 루트에는 `hri/`, `tests/`, `sql/`, `data/` 등만 둠 |
| 1단계 (패키지) | 기능명세서의 **패키지명 그대로** (`view`, `service`, `graph` …) |
| 2단계 (모듈) | 기능명세서의 **클래스명 = 파일명** (`qa_service.py`, `search.py` …) |
| 스키마 | `domain/schemas/` 패키지로 **영역별 파일 분리**, `__init__.py`에서 다시 내보냄 |
| 예외 | `exception/errors.py` 한 파일 |
| 실행 | 루트의 `app.py`가 `hri.view.app.main()`을 호출 |
| MySQL · Chroma | 서버에 띄운 것에 **연결만** 함 (서버 실행은 이 레포에서 다루지 않음) |
| Chroma 클라이언트 | `.env`의 `CHROMA_MODE`로 전환: `http`(서버, 기본) / `persistent`(로컬, 테스트·오프라인용) |
| 환율 | 문서 수치 검증은 ECOS, 토큰 비용 원화 환산은 한국수출입은행(`api/exchange_client.py`) |
| 담당 | WBS v4 **작업 행의 담당자 칸** 기준 (§4) |
| import 규칙 | 항상 절대 경로 `from hri.<패키지>.<모듈> import <이름>` |
| 코드 스타일 | `.github/CONTRIBUTING.md` 그대로 (PEP 8, 탭 들여쓰기, docstring 필수) |

## 1. 패키지 한눈에 보기

| 패키지 | 역할 | 모듈 |
|---|---|---|
| `view` | Gradio 탭과 이벤트 핸들러 | `app`, `report_view`, `chat_view`, `summary_view`, `indicator_view`, `term_view`, `user_view`, `history_view`, `admin_view` |
| `service` | 화면 요청 하나를 처리하는 업무 흐름 | `document_service`, `qa_service`, `summary_service`, `term_service`, `indicator_service`, `user_service`, `history_service`, `report_service` |
| `graph` | LangGraph 워크플로우 (업로드·질의응답·요약·Tool Calling) | `ingest_graph`, `qa_graph`, `summary_graph`, `agent_graph`, `checkpointer`, `registry`, `visualize` |
| `loader` | PDF 텍스트·표·차트 추출 | `pdf_loader`, `table_loader`, `chart_loader` |
| `splitter` | 섹션·청크 분할 | `text_splitter` |
| `vector_store` | 임베딩, Chroma 저장 | `embedding`, `chroma_store` |
| `retriever` | 키워드·의미·하이브리드 검색 | `search` |
| `prompt` | 프롬프트 구성 | `rag_prompt`, `summary_prompt`, `persona_prompt` |
| `memory` | 대화 기록 줄이기·질문 다시 쓰기 | `history_store` |
| `source` | 출처 정리 | `source_formatter` |
| `router` | Tool Calling 도구 목록·에이전트 실행 | `tool_router` |
| `llm` | LLM 모델 생성·호출·비용 계산 | `llm_client` |
| `api` | 외부 API (ECOS·수출입은행·국립국어원) | `ecos_client`, `exchange_client`, `stdict_client`, `ecos_finder` |
| `dao` | MySQL 연결과 SQL | `db`, `report_dao`, `user_dao`, `qa_log_dao`, `dashboard_dao`, `usage_dao`, `term_dao`, `indicator_dao` |
| `config` | 환경 설정 로드 | `settings` |
| `domain` | Pydantic 스키마 | `schemas/` |
| `exception` | 예외 | `errors` |
| `util` | 성능 측정·예외 기록 | `metrics`, `logger` |

## 2. 전체 트리

✅ 표시는 실제 코드가 들어 있는 파일입니다. 나머지는 담당자·함수 목록 docstring 한 줄만 있는 빈 모듈입니다.

```text
highlight-refresh-integrate/
├─ app.py                                  # 실행 파일: python app.py → hri.view.app.main()
├─ hri/
│  ├─ __init__.py
│  ├─ view/
│  │  ├─ app.py                            # build_app(), main(), init_state()
│  │  ├─ report_view.py                    # build_upload_tab(), on_upload(), build_report_list_panel()
│  │  ├─ chat_view.py                      # build_chat_tab(), respond_stream(), on_suggest_click(), on_feedback(), on_term_click()
│  │  ├─ summary_view.py                   # build_summary_tab(), on_summarize(), on_request()
│  │  ├─ indicator_view.py                 # build_indicator_panel(), on_compare()
│  │  ├─ term_view.py                      # build_term_panel(), on_term_search()
│  │  ├─ user_view.py                      # build_login_tab(), on_login(), on_register(), on_logout()
│  │  ├─ history_view.py                   # build_history_tab(), on_restore()
│  │  └─ admin_view.py                     # build_admin_tab(), on_refresh(), get_dashboard_data(), get_dashboard_data_v2(), plot_daily_counts()
│  │
│  ├─ service/
│  │  ├─ document_service.py               # upload_report(), validate_pdf()
│  │  ├─ qa_service.py                     # ask_stream(), validate_question(), mask_pii()
│  │  ├─ summary_service.py                # summarize_report()
│  │  ├─ term_service.py                   # explain_term()
│  │  ├─ indicator_service.py              # extract_indicators(), build_compare_table(), plot_indicators()
│  │  ├─ user_service.py                   # register(), login(), ensure_admin()
│  │  ├─ history_service.py                # save_qa_log(), get_history(), restore_session()
│  │  └─ report_service.py                 # search_reports(), get_report_text()
│  │
│  ├─ graph/
│  │  ├─ ingest_graph.py                   # build_ingest_graph(), route_after_validate(), load_text_node(), extract_tables_node(),
│  │  │                                    # process_charts_node(), split_node(), summarize_tables_node(), persist_node()
│  │  ├─ qa_graph.py                       # build_qa_graph(), route_question(), route_after_grade(), generate_node(),
│  │  │                                    # no_answer_node(), error_reply_node(), save_log_node()
│  │  ├─ summary_graph.py                  # build_summary_graph(), fan_out_sections()
│  │  ├─ agent_graph.py                    # build_agent_graph(), agent_node()
│  │  ├─ checkpointer.py                   # get_checkpointer()
│  │  ├─ registry.py                       # get_ingest_graph(), get_qa_graph(), get_summary_graph(), get_agent_graph()
│  │  └─ visualize.py                      # draw_graphs()
│  │
│  ├─ loader/
│  │  ├─ pdf_loader.py                     # load_pdf(), clean_text(), find_body_start(), extract_report_meta()
│  │  ├─ table_loader.py                   # extract_tables(), validate_table(), table_to_markdown(), split_big_table(), summarize_tables()
│  │  └─ chart_loader.py                   # extract_charts(), ocr_chart(), chart_to_table(), validate_chart(), chart_to_markdown()
│  ├─ splitter/
│  │  └─ text_splitter.py                  # split_sections(), split_text()
│  ├─ vector_store/
│  │  ├─ embedding.py                      # embed_texts(), similarity()
│  │  └─ chroma_store.py                   # ✅ get_collection() / add_chunks(), delete_report_vectors()
│  ├─ retriever/
│  │  └─ search.py                         # extract_keywords(), keyword_find(), vector_search(), safe_search(),
│  │                                       # mmr_select(), expand_queries(), hybrid_search()
│  │
│  ├─ prompt/
│  │  ├─ rag_prompt.py                     # format_docs(), has_context(), build_messages()
│  │  ├─ summary_prompt.py                 # summarize_sections(), summarize_three_lines(), verify_and_expand(), merge_reports()
│  │  └─ persona_prompt.py                 # PERSONAS, FEW_SHOT_EXAMPLES, get_persona_prompt()
│  ├─ memory/
│  │  └─ history_store.py                  # trim_history(), rewrite_question()
│  ├─ source/
│  │  └─ source_formatter.py               # format_sources()
│  ├─ router/
│  │  └─ tool_router.py                    # TOOLS, run_agent_with_tools()
│  ├─ llm/
│  │  └─ llm_client.py                     # get_chat_model(), ask_llm(), cost_korean_won(), get_usd_krw(), chat_bot()
│  │
│  ├─ api/
│  │  ├─ ecos_client.py                    # call_ecos(), get_ecos_exchange_rate(), get_ecos_statistic_table_list(),
│  │  │                                    # get_ecos_statistic_item_list(), get_ecos_statistic_data(), get_ecos_statistic_word()
│  │  ├─ exchange_client.py                # get_exchange_rate() (수출입은행, 토큰 비용 환산용)
│  │  ├─ stdict_client.py                  # get_stdict()
│  │  └─ ecos_finder.py                    # find_usable_data()
│  ├─ dao/
│  │  ├─ db.py                             # ✅ get_connection()
│  │  ├─ report_dao.py                     # insert_report(), insert_sections(), insert_chunks(), insert_tables(), select_sections(),
│  │  │                                    # update_summary_json(), get_context_docs(), select_reports()
│  │  ├─ user_dao.py                       # insert_user(), select_user(), select_user_auth()
│  │  ├─ qa_log_dao.py                     # insert_qa_log(), select_logs(), update_feedback()
│  │  ├─ dashboard_dao.py                  # select_top_questions(), select_source_ratio(), select_perf_cost(), select_recent_errors(),
│  │  │                                    # select_daily_counts(), select_persona_ratio(), select_feedback_ratio(), select_no_source_questions()
│  │  ├─ usage_dao.py                      # insert_usage()
│  │  ├─ term_dao.py                       # select_term(), insert_term()
│  │  └─ indicator_dao.py                  # select_indicators(), insert_indicators()
│  ├─ config/
│  │  └─ settings.py                       # ✅ load_settings()
│  │
│  ├─ domain/
│  │  └─ schemas/                          # Pydantic 스키마 (영역별 분리)
│  │     ├─ __init__.py                    # ✅ 전부 다시 내보냄 → from hri.domain.schemas import Chunk
│  │     ├─ report.py                      # Page, Section, Chunk, Table, Report, ValidationResult, ChartImage, OcrToken, ChartSeries, ChartData
│  │     ├─ qa.py                          # AnswerResult, SuggestOutput, SearchHit, ContextDoc
│  │     ├─ summary.py                     # ReportSummary, ThreeLineSummary, SectionSummary, VerifyResult, MergedReport
│  │     ├─ external.py                    # Indicator, EcosChoice, EcosQuery, TermCard, ExchangeRate
│  │     ├─ user.py                        # User, QALog, LlmUsage
│  │     ├─ dashboard.py                   # DashboardData
│  │     ├─ graph_state.py                 # IngestState, QAState, SummaryState, AgentState, RouteDecision
│  │     └─ settings.py                    # ✅ Settings (현재 MySQL·Chroma 항목만)
│  ├─ exception/
│  │  └─ errors.py                         # ChartExtractError, InvalidInputError, OutputFormatError, ExternalApiError,
│  │                                       # LoginFailedError, DuplicateUserError
│  └─ util/
│     ├─ metrics.py                        # measure_time()
│     └─ logger.py                         # log_error(), safe_call()
│
├─ tests/
│  ├─ fakes.py                             # FakeToolChatModel (bind_tools·with_structured_output 구현)
│  ├─ sample_pdf.py                        # make_sample_pdf() (삭제 예정)
│  ├─ test_graphs.py                       # test_*_graph()
│  └─ fixtures/                            # sample_chart_answers.json (make_sample_pdf가 생성)
├─ sql/
│  └─ schema.sql                           # users / reports / sections / chunks / report_tables / qa_logs / terms / indicators / llm_usage + VIEW v_daily_usage
├─ data/                                   # 폴더만 커밋, 내용은 .gitignore
│  ├─ raw/                                 # 업로드한 원본 PDF
│  ├─ charts/raw/                          # extract_charts()가 잘라낸 차트 PNG
│  ├─ plots/                               # plot_indicators()·plot_daily_counts() 결과 PNG
│  └─ ecos/                                # get_ecos_statistic_table_list() 캐시
├─ assets/
│  └─ fonts/                               # matplotlib 한글 폰트 (커밋 대상)
├─ docs/
│  └─ graphs/                              # draw_graphs()가 만드는 그래프 구조도 (Mermaid)
├─ chroma_db/                              # CHROMA_MODE=persistent일 때만 생김 (.gitignore)
├─ logs/                                   # app.log, 실행 중 생김 (.gitignore)
├─ .env.example
└─ pyproject.toml / poetry.lock
```

- 모든 패키지 폴더에 `__init__.py`가 있습니다. (트리에서는 생략)
- 각 모듈의 첫 줄 docstring에 역할·함수 목록·담당이 있습니다. (예: `"""질의응답 처리 · ask_stream (장유빈) / validate_question, mask_pii (서동안)"""`) 구현하면서 함수나 담당이 바뀌면 docstring도 함께 고쳐 주세요.
- 스키마 파일에 모델을 추가하면 그 파일의 `__all__`에 이름을 넣어야 `from hri.domain.schemas import ...`로 쓸 수 있습니다.

## 3. MySQL · Chroma 연결

서버는 따로 띄우고, 이 레포는 `.env`의 접속 정보로 연결만 합니다.

| 파일 | 내용 |
|---|---|
| `hri/config/settings.py` | `load_settings()`: `.env`를 한 번 읽어 `Settings`로 반환 (캐시). 필수 키가 없으면 어떤 키가 빠졌는지 알려줌. 테스트에서 환경 변수를 바꿨다면 `load_settings.cache_clear()` |
| `hri/domain/schemas/settings.py` | `Settings`: 현재 MySQL·Chroma 항목만. API 키·모델·OCR 등은 담당자가 필드를 추가 |
| `hri/dao/db.py` | `get_connection()`: `pymysql.connect(charset='utf8mb4', cursorclass=DictCursor)`. `with` 문으로 쓰면 닫힘. autocommit이 꺼져 있어 commit·rollback은 호출부에서 |
| `hri/vector_store/chroma_store.py` | `get_collection()`: `CHROMA_MODE`에 따라 `HttpClient` / `PersistentClient`, 컬렉션 `hri_reports`(cosine). 임베딩은 `embed_texts()`로 직접 넣으므로 `embedding_function=None` |

> Chroma 클라이언트(`chromadb` 패키지)와 서버의 메이저 버전이 같아야 합니다.

## 3-1. `.env` 항목

`.env.example`을 `.env`로 복사한 뒤 값을 채웁니다. 항목은 기능명세서 `load_settings()` 기준입니다.
현재 `load_settings()`는 **MySQL·Chroma 항목만** 읽습니다. 나머지는 `Settings`에 필드를 추가하면서 함께 읽도록 바꿉니다.

| 구분 | 키 | 값 (`.env.example`) | 비고 |
|---|---|---|---|
| API 키 | `OPENAI_API_KEY` | 직접 입력 | |
| | `ECOS_API_KEY` | 직접 입력 | 한국은행 ECOS |
| | `STDICT_API_KEY` | 직접 입력 | 국립국어원 표준국어대사전 |
| | `KOREAEXIM_API_KEY` | 직접 입력 | 한국수출입은행 (토큰 비용 환산) |
| MySQL | `MYSQL_HOST`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE` | 직접 입력 | 필수 ✅ |
| | `MYSQL_PORT` | `3306` | ✅ |
| Chroma | `CHROMA_MODE` | `http` | `http`(서버) / `persistent`(로컬) ✅ |
| | `CHROMA_HOST` | 직접 입력 | `http` 모드에서 필수 ✅ |
| | `CHROMA_PORT` | `8000` | ✅ |
| | `CHROMA_PERSIST_PATH` | `chroma_db/` | `persistent` 모드 ✅ |
| | `CHROMA_COLLECTION` | `hri_reports` | ✅ |
| 모델·단가 | `LLM_MODEL` | `gpt-4o-mini` | |
| | `VISION_MODEL` | (빈칸) | 명세에 기본값 없음 |
| | `MODEL_PRICES` | `'{"gpt-4o-mini": {"input": null, "output": null}}'` | 모델별 1M 토큰당 USD (JSON 한 줄). `null`은 가격표 확인 후 입력 |
| 업로드 한도 | `MAX_MB`, `MAX_PAGES` | `30`, `150` | |
| OCR·차트 | `OCR_LANGS`, `OCR_MIN_CONF`, `CHART_DPI`, `MIN_CHART_PT` | `ko,en`, `0.4`, `200`, `120` | |
| 검색 | `CHUNK_SIZE`, `CHUNK_OVERLAP`, `THRESHOLD` | `500`, `50`, `0.3` | 테스트하며 조정 |
| | `MAX_CONTEXT_CHARS` | (빈칸) | 명세에 기본값 없음 |
| 그래프 | `GRAPH_RECURSION_LIMIT` | `8` | |
| 관리자 (선택) | `ADMIN_ID`, `ADMIN_PASSWORD` | 주석 처리 | 둘 다 있으면 앱 시작 시 관리자 계정 생성 |
| LangSmith (선택) | `LANGSMITH_TRACING`, `LANGSMITH_API_KEY` | 주석 처리 | 둘 다 있을 때만 추적 |

✅ = 현재 `load_settings()`가 읽는 항목

## 4. 모듈별 담당 (WBS v4 작업 행 기준)

담당은 WBS v4 **작업 행의 담당자 칸**을 따릅니다. 한 파일을 여러 명이 맡으면 함수 단위로 나눕니다. (`모듈.함수`로 표기)

| 담당 | 모듈 (`hri/` 생략) |
|---|---|
| 류현수 | `api/exchange_client`, `api/stdict_client`, `api/ecos_finder`, `service/term_service`, `dao/term_dao`, `view/term_view`, `view/indicator_view.build_indicator_panel`, `view/chat_view.on_suggest_click`·`on_term_click`·`on_feedback`, `dao/qa_log_dao.update_feedback`, `tests/sample_pdf`(삭제 예정) |
| 서동안 | `service/qa_service.validate_question`·`mask_pii`, `prompt/persona_prompt`, `llm/llm_client.get_chat_model`·`ask_llm`·`chat_bot`, `domain/schemas/qa` |
| 김희진 | `view/report_view.build_upload_tab`·`on_upload`, `view/summary_view`, `service/document_service.upload_report`, `service/summary_service`, `splitter/text_splitter`, `vector_store/*`, `dao/report_dao.insert_report`·`insert_sections`·`insert_chunks`·`select_sections`·`update_summary_json`·`get_context_docs`, `retriever/search.extract_keywords`·`keyword_find`, `prompt/summary_prompt`, `router/tool_router`, `domain/schemas/summary` |
| 엄정현 | `loader/*`, `service/document_service.validate_pdf`, `dao/report_dao.insert_tables`, `api/ecos_client`, `graph/*`, `domain/schemas/report`·`external`·`graph_state`, `tests/fakes`, `tests/test_graphs` |
| 임세준 | `view/app`, `view/user_view`, `view/history_view`, `view/admin_view`, `service/user_service`, `service/history_service`, `dao/user_dao`, `dao/qa_log_dao`, `dao/dashboard_dao`, `dao/usage_dao`, `config/settings`, `llm/llm_client.cost_korean_won`·`get_usd_krw`, `util/*`, `domain/schemas/user`·`dashboard`·`settings` |
| 장유빈 | `view/chat_view.build_chat_tab`·`respond_stream`, `view/report_view.build_report_list_panel`, `view/indicator_view.on_compare`, `service/qa_service.ask_stream`, `service/report_service`, `service/indicator_service`, `retriever/search.vector_search`·`safe_search`·`mmr_select`·`expand_queries`·`hybrid_search`, `prompt/rag_prompt`, `memory/history_store`, `source/source_formatter`, `dao/db`, `dao/report_dao.select_reports`, `dao/indicator_dao`, `sql/schema.sql` |
| 각 영역 | `exception/errors`: `ChartExtractError`·`ExternalApiError`(엄정현), `InvalidInputError`(서동안), `OutputFormatError`(김희진), `LoginFailedError`·`DuplicateUserError`(임세준) |

여러 명이 함께 맡는 파일: `view/report_view`, `view/chat_view`, `view/indicator_view`, `service/document_service`, `service/qa_service`, `dao/report_dao`, `retriever/search`, `llm/llm_client`, `exception/errors`. 같은 파일을 동시에 고치게 되므로 PR을 작게 나눠 주세요.

> - `graph/*`: 작업 행(3.8.1~3.8.9) 기준으로 **엄정현**입니다. WBS 상단 "담당파트" 요약에는 그래프가 여러 명으로 나뉘어 있어, 작업 행에 맞춰 v4를 직접 고치기로 했습니다.
> - `tests/sample_pdf`(`make_sample_pdf`): **삭제 예정**입니다. 삭제하면 `test_*_graph()`의 ingest 통합 테스트와 차트 정확도 테스트(`sample_chart_answers.json`)에 쓸 입력 PDF를 대신할 방법이 필요합니다.
> - `view/report_view.build_report_list_panel`: 보고서 탭의 "보고서 목록·검색" 패널(키워드 검색 → 발행일 최신순 목록)입니다. WBS **3.6.13(신규, 장유빈)**으로 추가하고, 기능명세서 문서처리 시트 `report_view`에 메소드 행을 추가해야 합니다.

## 5. 의존성 (pyproject 추가 예정 · 별도 PR)

아직 `pyproject.toml`에 추가하지 않았습니다. 기능명세서의 의존성 목록은 다음과 같습니다.

- 런타임: `gradio>=5.30,<6`, `langgraph>=1.0`, `langchain-core`, `langchain-openai`, `openai`, `chromadb`, `pypdf`, `pdfplumber`, `pymupdf`, `easyocr`(CPU용 torch 휠), `pymysql`, `python-dotenv`, `pydantic>=2`, `numpy`, `pandas`, `matplotlib`
- 개발용(`--group dev`): `pytest`, `reportlab`(샘플 PDF 생성)

## 6. 실행 · 테스트

```bash
poetry install
poetry run python app.py             # http://localhost:7860 (main() 구현 후)
poetry run python -m pytest          # pytest 추가 후
```

- 항상 **레포 루트에서** 실행합니다. `package-mode = false`라서 `hri` 패키지가 설치되지 않으므로, 루트가 import 경로에 있어야 합니다. (`pytest` 대신 `python -m pytest`를 쓰는 이유)

