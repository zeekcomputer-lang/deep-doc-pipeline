# Deep Doc Pipeline (v3.0)

JSONL 문서 → 영문 분석·집필 → 한국어 백서 + 전문 DOCX 자동 생성 파이프라인.
LangGraph + OpenAI SDK + Pydantic 강제 출력.

## 문서

- **[`HANDOFF.md`](./HANDOFF.md)** — 인수인계 (아키텍처·방어 기제·시나리오)
- **[`SPEC.md`](./SPEC.md)** — 설계 명세서 v3.0
- **[`LESSONS.md`](./LESSONS.md)** — 누적 교훈 (L-011~L-023)

## 구조

```
├── main.py                  실행 진입점
├── data/records.jsonl       입력 JSONL (gen_dummy.py로 생성)
├── scripts/
│   ├── gen_dummy.py         더미 데이터 생성기
│   └── md_to_docx.py       마크다운 → DOCX 변환 (독립 스크립트)
└── src/
    ├── prompt_config.py     ★ 사용자 커스텀 프롬프트 설정
    ├── schemas.py           Pydantic 응답 스키마 (7종)
    ├── state.py             GraphState + reducer
    ├── llm.py               OpenAI SDK 클라이언트 + Rate Limiter
    ├── context_guard.py     95KB 컨텍스트 예산 관리
    ├── logger.py            타임라인 로거 + 실행 통계
    ├── utils.py             Pure Python 결정론 로직
    ├── nodes.py             LangGraph 노드 (15개 + 라우터 4개)
    ├── graph.py             그래프 조립
    ├── artifacts.py         중간 산출물 저장
    └── docx_builder.py      전문 DOCX 빌더 (표지+본문2p)
```

## 셋업

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # OPENAI_BASE_URL, OPENAI_MODEL 편집
```

### 인증

`src/llm.py`의 `DEFAULT_HEADERS`에서 필요한 헤더 주석 해제 후 값 입력.
또는 환경변수로 주입:
```bash
export OPENAI_EXTRA_HEADERS='{"Authorization": "Bearer xxx"}'
```

## 실행

```bash
python -m scripts.gen_dummy                     # 더미 데이터 생성 (1회)
python -m main                                  # 백서 + DOCX 생성 (기본)
python -m main --reasoning medium               # 서버 타임아웃 회피 우선
python -m main --skip-fact-check                # 팩트체크 생략 (빠른 실행)
python -m main --output out.md                  # 한글 백서 추가 저장
python -m main --output-docx report.docx        # DOCX 추가 저장
```

### 출력 예시

```
======================================================================
Deep Doc Pipeline v3.0 — Whitepaper Generator (EN→KR+DOCX)
모델: gpt-oss:20b @ http://localhost:11434/v1
추론: high | 팩트체크 ON | 504 2회 초과 시 medium 자동 전환
산출물: output/20260609_164400/
======================================================================
[00:00] #1   [load_docs] loaded=15 failed=0
[00:03] #2   [chrono_sorter] events=15 months=["2026-02", ...]
[00:18] #8   [strategic_analyst] blueprint: 'AI Growth Report' | ...
[00:45] #12  [translate] done: en=1600 kr=1200 ratio=0.75
[00:50] #13  [docx_builder] DOCX saved: output/20260609_164400/output.docx

======================================================================
✅ 파이프라인 완료
======================================================================
  총 소요 시간 : 0분 50초
  완료 작업 수 : 13건
  LLM API 호출 : 22건
  블루프린트   : AI Growth Report
  섹션 수      : 2
  영문 원본    : 1,600 chars
  한글 번역    : 1,200 chars
  DOCX 경로    : output/20260609_164400/output.docx
======================================================================
```

## 환경변수

| 변수 | 기본값 | 설명 |
|------|-------|------|
| `OPENAI_BASE_URL` | — | LLM 엔드포인트 URL |
| `OPENAI_MODEL` | gpt-oss-20b | 기본 모델 |
| `EXTRACTOR_MODEL` | (OPENAI_MODEL) | 추출 전용 모델 |
| `JUDGE_MODEL` | (OPENAI_MODEL) | 팩트체크 전용 모델 |
| `WRITER_MODEL` | (OPENAI_MODEL) | 집필 전용 모델 |
| `LLM_MAX_RPM` | 12 | 분당 최대 호출 수 |
| `LLM_MAX_CONCURRENT` | 5 | 동시 호출 상한 |
| `LLM_CONTEXT_BUDGET_KB` | 95 | per-call 컨텍스트 예산 (KB) |

## 프롬프트 커스텀

`src/prompt_config.py` 편집으로 백서 톤·목적·표지 설정:

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `DOCUMENT_PURPOSE` | 기간별 이벤트 백서 | 문서 목적 |
| `TONE_DIRECTIVE` | (중립) | 톤/편향 |
| `TARGET_AUDIENCE` | (일반) | 대상 독자 |
| `CUSTOM_DIRECTIVES` | (없음) | 집필 추가 지시 |
| `DOCUMENT_TITLE` | (LLM 생성) | DOCX 표지 제목 |
| `ORGANIZATION_NAME` | (없음) | DOCX 표지 조직명 |

## 출력 구조

```bash
output/YYYYMMDD_HHMMSS/
├── phase1_extracted_events.json    # 추출 이벤트
├── phase1_grouped_chunks.json      # 월별 그루핑
├── phase3_blueprint.json           # 전략 블루프린트
├── phase4_sections/                # 섹션별 초안
├── phase4_compiled_en.md           # 영문 조립본
├── phase4_polished_en.md           # 영문 윤문본
├── phase5_output_kr.md             # 한글 백서
├── phase5_output_en.md             # 영문 원본
└── output.docx                     # 전문 DOCX (표지+본문2p)
```

## 핵심 방어 기제

| 위험 | 방어 |
|------|------|
| Fact-checker 회귀 | `previous_draft` + `hallucinated_tokens` 블랙리스트 주입 |
| Fail-Safe 강제통과 | ⚠️ 워터마크 삽입 |
| 고유명사 보존 | `extract_proper_nouns` → 번역 프롬프트에 목록 주입 |
| 504 타임아웃 | 국부 감축(-5KB/step) + 노드 재실행 + 성공 후 원복 |
| 95KB 초과 | 사전 측정 + 배치 분할 + 병합 |
