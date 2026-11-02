# HRI Insight Bridge · 경제분석보고서 AI 요약 서비스

경제분석보고서(PDF)를 올리면 요약·질의응답·지표 확인을 해 주는 Gradio 웹 앱입니다.
별도 서버 없이 **내 PC(로컬)에서** 실행할 수 있습니다.

- 화면: Gradio (`http://localhost:7860`)
- 벡터 DB: Chroma (로컬 폴더 `chroma_db/`에 저장)
- RDB: MySQL 8.0 이상 (로컬에 설치)
- LLM·임베딩: OpenAI API

## 1. 준비물

| 항목 | 버전·비고 |
|---|---|
| Python | **3.11** (3.12 이상 불가) |
| MySQL | 8.0 이상 |
| OpenAI API 키 | 필수 |
| ECOS API 키 | 지표·환율 패널, 수치 검증용 (한국은행 ECOS) |
| 한국수출입은행 API 키 | 토큰 비용 원화 환산용 (없으면 해당 기능만 제한) |

## 2. 설치

```bash
# 압축을 푼 폴더에서
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

설치에는 PyTorch(EasyOCR 의존성)가 포함되어 수 분이 걸립니다. Linux에서는 CUDA 패키지까지 받아 수 GB가 필요할 수 있습니다.

Poetry를 쓰는 경우 `poetry env use 3.11` → `poetry install`로도 같은 환경을 만들 수 있습니다.

## 3. DB 준비 (MySQL)

```sql
CREATE DATABASE hyb_db DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
```

```bash
mysql -u root -p hyb_db < sql/schema.sql
```

`schema.sql`은 여러 번 실행해도 안전합니다(없는 테이블만 생성).

## 4. 환경 변수

```bash
# Windows (PowerShell)
Copy-Item .env.example .env
# macOS / Linux
cp .env.example .env
```

`.env`에서 최소한 아래 값을 채웁니다. 나머지는 기본값 그대로 두면 됩니다.

| 키 | 설명 |
|---|---|
| `OPENAI_API_KEY` | OpenAI API 키 |
| `ECOS_API_KEY`, `KOREAEXIM_API_KEY` | 외부 지표·환율 API 키 |
| `MYSQL_USER`, `MYSQL_PASSWORD` | 로컬 MySQL 계정 |
| `CHROMA_MODE` | `persistent` 유지 (로컬 폴더 사용) |

## 5. 실행

```bash
python app.py
```

브라우저에서 `http://localhost:7860` 접속 → **회원가입** 후 로그인합니다.

- 처음 실행하면 Chroma가 비어 있으므로 **보고서(PDF)를 먼저 업로드**해야 요약·질의응답을 사용할 수 있습니다.
- 첫 PDF 처리 시 EasyOCR이 OCR 모델을 내려받기 때문에 인터넷 연결과 약간의 시간이 필요합니다.
- 관리자 화면을 쓰려면 가입한 계정의 역할을 바꿉니다.
  ```sql
  UPDATE users SET role = 'admin' WHERE user_id = '<아이디>';
  ```

## 6. 폴더 구조

```
app.py            실행 파일 (hri.view.app.main 호출)
hri/              애플리케이션 패키지
  view/           Gradio 화면
  service/        화면 요청 처리 흐름
  graph/          LangGraph 그래프 (수집·요약·QA·에이전트)
  loader/ splitter/ vector_store/ retriever/   문서 처리·RAG
  dao/            MySQL 접근
  api/            ECOS·환율 외부 API
  domain/schemas/ Pydantic 모델
sql/schema.sql    MySQL 스키마
docs/             설계 문서
tests/            테스트·평가 스크립트
assets/           폰트·로고
```

자세한 구조는 `docs/project_structure.md`를 참고하세요.
