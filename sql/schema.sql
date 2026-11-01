-- =====================================================================
-- HRI Insight Bridge · sql/schema.sql
-- MySQL 8.0 이상 / InnoDB / utf8mb4
--
-- 실행 방법 (DB는 미리 만들어 두기)
--   CREATE DATABASE hyb_db DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
--   mysql -u <사용자> -p hyb_db < sql/schema.sql
--
-- 여러 번 실행해도 안전해요: 이미 있는 테이블은 건드리지 않고(데이터 유지),
-- 없는 테이블만 만들어요.
-- 주의: 이미 만들어진 테이블의 구조(칸 추가·수정)는 이 파일을 다시 실행해도 바뀌지 않아요.
--       구조를 바꿔야 하면 팀에 공유한 뒤 ALTER TABLE을 쓰거나 reset.sql 후 다시 실행하세요.
-- 전부 지우고 새로 만들려면: sql/reset.sql 실행 → sql/schema.sql 실행
--
-- 설계 원칙
--   - 회원 PK는 로그인 아이디(users.user_id)
--   - sections·chunks는 (report_id, 순번) 복합 PK. Chroma id = '{report_id}-{chunk_no}'
--   - 보고서 삭제 기능 없음 → FK에 ON DELETE CASCADE를 걸지 않음(기본값 RESTRICT)
--   - 토큰·비용은 qa_logs(답변별)와 llm_usage(흐름별) 두 곳에 저장
-- =====================================================================

SET NAMES utf8mb4;

-- ---------------------------------------------------------------------
-- 1. 회원
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    user_id        VARCHAR(50)  NOT NULL COMMENT '로그인 아이디',
    name           VARCHAR(50)  NOT NULL,
    password       VARCHAR(255) NOT NULL COMMENT '해시 사용 안 함',
    role           ENUM('user','admin') NOT NULL DEFAULT 'user',
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='회원';


-- ---------------------------------------------------------------------
-- 2. 보고서
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reports (
    report_id       INT          NOT NULL AUTO_INCREMENT,
    title           VARCHAR(255) NOT NULL,
    publisher       VARCHAR(100) NOT NULL DEFAULT '현대경제연구원',
    published_date  DATE         NULL     COMMENT '못 찾으면 NULL',
    page_count      INT          NOT NULL DEFAULT 0,
    file_path       VARCHAR(255) NOT NULL,
    file_hash       VARCHAR(36)  NOT NULL COMMENT '파일 식별값(uuid), UNIQUE',
    summary_json    JSON         NULL     COMMENT '요약 캐시(ReportSummary)',
    uploaded_by     VARCHAR(50)  NOT NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (report_id),
    UNIQUE KEY uk_reports_file_hash (file_hash),
    KEY idx_reports_published_date (published_date),
    CONSTRAINT fk_reports_user
        FOREIGN KEY (uploaded_by) REFERENCES users (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='업로드한 보고서. 청크 수는 chunks를 집계해서 구함';


CREATE TABLE IF NOT EXISTS sections (
    report_id   INT          NOT NULL,
    section_no  INT          NOT NULL COMMENT '보고서 안 순번, 0부터',
    title       VARCHAR(255) NOT NULL,
    start_page  INT          NOT NULL COMMENT '1부터',
    end_page    INT          NOT NULL,
    text        MEDIUMTEXT   NOT NULL COMMENT '섹션 원문(답변 근거)',
    PRIMARY KEY (report_id, section_no),
    CONSTRAINT fk_sections_report
        FOREIGN KEY (report_id) REFERENCES reports (report_id),
    CONSTRAINT ck_sections_pages CHECK (start_page >= 1 AND end_page >= start_page)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='목차 단위 부모 섹션';


CREATE TABLE IF NOT EXISTS report_tables (
    doc_id          VARCHAR(36)  NOT NULL COMMENT 'uuid',
    report_id       INT          NOT NULL,
    page            INT          NOT NULL,
    caption         VARCHAR(255) NULL,
    markdown        MEDIUMTEXT   NOT NULL COMMENT '답변용 원본 표',
    summary         TEXT         NULL     COMMENT '검색용 1~2문장 요약',
    source_type     ENUM('table','chart') NOT NULL DEFAULT 'table',
    image_path      VARCHAR(255) NULL     COMMENT '차트만',
    ocr_confidence  FLOAT        NULL     COMMENT '차트만 (match_ratio)',
    needs_review    BOOLEAN      NOT NULL DEFAULT FALSE COMMENT 'TRUE면 추정값 표시',
    PRIMARY KEY (doc_id),
    KEY idx_report_tables_report (report_id, page),
    CONSTRAINT fk_report_tables_report
        FOREIGN KEY (report_id) REFERENCES reports (report_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='보고서 속 표·차트 원본';


-- insert 순서: insert_tables() → insert_chunks() (doc_id FK)
CREATE TABLE IF NOT EXISTS chunks (
    report_id   INT          NOT NULL,
    chunk_no    INT          NOT NULL COMMENT '보고서 안 순번, 0부터',
    section_no  INT          NOT NULL,
    page        INT          NOT NULL,
    chunk_type  ENUM('text','table','chart') NOT NULL DEFAULT 'text',
    category    VARCHAR(50)  NULL,
    text        TEXT         NOT NULL COMMENT '본문, 또는 표·차트의 캡션+요약',
    doc_id      VARCHAR(36)  NULL     COMMENT '표·차트 청크만',
    PRIMARY KEY (report_id, chunk_no),
    KEY idx_chunks_section (report_id, section_no),
    KEY idx_chunks_doc (doc_id),
    CONSTRAINT fk_chunks_section
        FOREIGN KEY (report_id, section_no) REFERENCES sections (report_id, section_no),
    CONSTRAINT fk_chunks_table
        FOREIGN KEY (doc_id) REFERENCES report_tables (doc_id),
    CONSTRAINT ck_chunks_doc_id CHECK (
        (chunk_type = 'text' AND doc_id IS NULL) OR
        (chunk_type IN ('table','chart') AND doc_id IS NOT NULL)
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='검색 단위 자식 청크';


-- ---------------------------------------------------------------------
-- 3. 지표 (용어 풀이 결과는 DB에 저장하지 않고 앱 메모리에만 기억)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS indicators (
    indicator_id  INT          NOT NULL AUTO_INCREMENT,
    report_id     INT          NOT NULL,
    name          VARCHAR(100) NOT NULL COMMENT 'INDICATOR_ALIASES로 통일한 이름',
    value         DOUBLE       NOT NULL,
    unit          VARCHAR(20)  NULL     COMMENT '%, %p, 억 원, 조 원',
    period        VARCHAR(20)  NULL     COMMENT '예: 2027, 2027Q1',
    page          INT          NULL     COMMENT '출처 쪽(1부터). 표·차트는 그 쪽, 본문은 섹션 시작 쪽',
    source        ENUM('text','table','chart') NOT NULL DEFAULT 'text',
    needs_review  BOOLEAN      NOT NULL DEFAULT FALSE COMMENT 'TRUE면 비교표에 *',
    PRIMARY KEY (indicator_id),
    KEY idx_indicators_report (report_id, name),
    CONSTRAINT fk_indicators_report
        FOREIGN KEY (report_id) REFERENCES reports (report_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='보고서별 추출 지표 (비교표·차트용)';


-- ---------------------------------------------------------------------
-- 4. 대화·질문 기록
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id  VARCHAR(36)  NOT NULL COMMENT 'uuid4, qa_graph thread_id와 같은 값',
    user_id     VARCHAR(50)  NOT NULL,
    status      ENUM('active','closed') NOT NULL DEFAULT 'active'
                COMMENT 'active는 사용자당 최대 MAX_SESSIONS(3)개 - 앱에서 트랜잭션으로 확인',
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at   DATETIME     NULL,
    PRIMARY KEY (session_id),
    KEY idx_chat_sessions_user_status (user_id, status),
    CONSTRAINT fk_chat_sessions_user
        FOREIGN KEY (user_id) REFERENCES users (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='대화(세션). 로그아웃 시 전부 closed';


CREATE TABLE IF NOT EXISTS chat_session_reports (
    session_id  VARCHAR(36)  NOT NULL,
    report_id   INT          NOT NULL,
    PRIMARY KEY (session_id, report_id),
    KEY idx_chat_session_reports_report (report_id),
    CONSTRAINT fk_csr_session
        FOREIGN KEY (session_id) REFERENCES chat_sessions (session_id),
    CONSTRAINT fk_csr_report
        FOREIGN KEY (report_id) REFERENCES reports (report_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='대화마다 고른 보고서 (질문 시 report_ids)';


CREATE TABLE IF NOT EXISTS qa_logs (
    log_id       INT          NOT NULL AUTO_INCREMENT,
    user_id      VARCHAR(50)  NOT NULL,
    session_id   VARCHAR(36)  NOT NULL,
    question     TEXT         NOT NULL COMMENT 'mask_pii() 적용 후',
    answer       TEXT         NOT NULL COMMENT 'mask_pii() 적용 후',
    persona      VARCHAR(10)  NOT NULL DEFAULT '실무자' COMMENT '입문자/실무자/전문가',
    route        ENUM('rag','no_answer','chat','error') NOT NULL,
    has_source   BOOLEAN      NOT NULL DEFAULT FALSE,
    response_ms  INT          NULL,
    tokens       INT          NOT NULL DEFAULT 0,
    cost_krw     DECIMAL(10,2) NOT NULL DEFAULT 0,
    feedback     TINYINT      NULL     COMMENT '1=👍, -1=👎, NULL=없음',
    error        TEXT         NULL,
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (log_id),
    KEY idx_qa_logs_user_created (user_id, created_at),
    KEY idx_qa_logs_session (session_id, created_at),
    KEY idx_qa_logs_route (route),
    KEY idx_qa_logs_created (created_at),
    CONSTRAINT fk_qa_logs_user
        FOREIGN KEY (user_id) REFERENCES users (user_id),
    CONSTRAINT fk_qa_logs_session
        FOREIGN KEY (session_id) REFERENCES chat_sessions (session_id),
    CONSTRAINT ck_qa_logs_feedback CHECK (feedback IS NULL OR feedback IN (1, -1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='질문 1건 = 1행 (대시보드 데이터 원천)';


CREATE TABLE IF NOT EXISTS qa_log_sources (
    log_id      INT          NOT NULL,
    ref_no      INT          NOT NULL COMMENT '답변 속 [n]의 n',
    report_id   INT          NOT NULL,
    section_no  INT          NULL,
    doc_id      VARCHAR(36)  NULL     COMMENT '표·차트 출처일 때',
    page_start  INT          NOT NULL,
    page_end    INT          NOT NULL,
    PRIMARY KEY (log_id, ref_no),
    KEY idx_qa_log_sources_report (report_id),
    CONSTRAINT fk_qls_log
        FOREIGN KEY (log_id) REFERENCES qa_logs (log_id),
    CONSTRAINT fk_qls_report
        FOREIGN KEY (report_id) REFERENCES reports (report_id),
    CONSTRAINT ck_qls_ref_no CHECK (ref_no >= 1),
    CONSTRAINT ck_qls_pages CHECK (page_end >= page_start)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='답변에서 실제로 인용한 출처만 저장';


CREATE TABLE IF NOT EXISTS llm_usage (
    usage_id       INT          NOT NULL AUTO_INCREMENT,
    flow           ENUM('qa','ingest','summary','agent') NOT NULL,
    user_id        VARCHAR(50)  NULL,
    ref_id         INT          NULL     COMMENT 'flow에 따라 log_id 또는 report_id (FK 없음)',
    model          VARCHAR(50)  NOT NULL,
    input_tokens   INT          NOT NULL DEFAULT 0,
    output_tokens  INT          NOT NULL DEFAULT 0,
    cost_krw       DECIMAL(10,2) NOT NULL DEFAULT 0,
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (usage_id),
    KEY idx_llm_usage_flow_created (flow, created_at),
    CONSTRAINT fk_llm_usage_user
        FOREIGN KEY (user_id) REFERENCES users (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='흐름별 LLM 토큰·원화 비용';


-- ---------------------------------------------------------------------
-- 5. VIEW (대시보드 2차 일별 추이)
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_daily_usage AS
SELECT
    DATE(created_at)  AS day,
    COUNT(*)          AS question_count,
    AVG(response_ms)  AS avg_response_ms
FROM qa_logs
GROUP BY DATE(created_at);
