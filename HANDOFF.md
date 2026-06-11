# HANDOFF.md — 다음 AI Agent 인수인계 문서

> **프로젝트:** deep-doc-pipeline (v3.1)
> **GitHub:** https://github.com/zeekcomputer-lang/deep-doc-pipeline (PUBLIC)
> **로컬:** `~/.openclaw/workspace/projects/deep-doc-pipeline/`
> **최종 업데이트:** 2026-06-11
> **상태:** v3.1 코드 완성 + AST 13/13 PASS / **실제 LLM 실행 미수행** (사용자 환경 보류)

---

## §0. 30초 요약

저성능 LLM(gpt-oss) 환경에서 **환각을 구조적으로 차단**하며 200건 JSONL → **한글 백서 + 전문 DOCX**를 자동 생성하는 **LangGraph 파이프라인**.

```
JSONL → 추출·압축·전략분석 → 2개 내러티브 섹션 집필 → 조립·시사점·윤문 → 한국어 번역 → DOCX 빌더
```

**규모:** 12 Python 파일 / 3,321줄 (src 2,851 + scripts 316 + main 135 + artifacts 76). AST 전 파일 통과.
**LLM 호출:** ~20-30회 (v2.0 대비 60~80% 절감).

---

## §1. 버전 히스토리

| 버전 | 커밋 | 핵심 변경 |
|------|------|----------|
| **v3.1** | — | implications_writer 신설 + 시사점 자동 생성 + 본문 연속 흐름 + 한글 제목 번역 |
| v3.0 | `5510578` | 3-page DOCX + 2 내러티브 섹션 + strategic_analyst + docx_builder |
| v2.0 | `09fda16` | 번역 v2 + prompt_config 커스텀 + --skip-fact-check + 에러로그 |
| v1.5 | `50ed4fd` | 경량 워크플로우: 비교/검증 루프 제거 + DOCX 변환 스크립트 |
| v1.4 | `a6677ea` | 504 국부 감축 + max_tokens + reasoning + 영문 분리 |
| v1.3 | `4d776f1` | EN-only LLM + 백서 전용 + 수석 에디터 렌더링 |
| v1.1 | `73d6d9c` | 초기 구현 — 구조적 위험 3건 보강 |

### v3.1 (현재)

**시사점 자동 생성 + DOCX 개선:**

**핵심 변경:**
- `implications_writer_node` 신설 — 블루프린트의 `implications_points`를 기반으로 시사점 섹션 자동 생성.
- `DocumentBlueprint`에 `implications_points` 필드 추가.
- `GraphState`에 `implications_text`, `doc_title_kr`, `doc_subtitle_kr` 3개 필드 추가.
- `prompt_config.py`에 `DOMAIN_KNOWLEDGE`, `IMPLICATIONS_DIRECTIVE` 설정 추가.
- `translate_node` — 제목/부제 한글 번역 포함 (`[제목]`/`[부제]` 추출).
- `docx_builder` — 본문 연속 흐름(페이지 분리 제거) + 시사점 섹션 + 한글 제목/부제.
- 그래프 흐름: assembler → implications_writer → polish.
- 노드 수: 15+4 → 16+4. 코드량: 3,119줄 → 3,321줄.

### v3.0

**아키텍처 전면 개편:** 월별 N개 섹션 → 테마 기반 고정 2섹션.

**핵심 변경:**
- `strategic_analyst_node` 신설 — theme_analyzer + draft_planner + planner_critique 루프를 단일 노드로 통합. `DocumentBlueprint` (제목, 부제, 2개 `SectionPlan`) 생성.
- `docx_builder_node` + `DocxBuilder` 신설 — 표지(네이비 타이틀+악센트 라인) + 본문 2페이지 + 헤더/푸터/페이지 번호. python-docx + lxml.
- `period_digest` (구 `period_summarizer`) — 3문장 요약 → 1~2문장 다이제스트 + event_count + key_metrics[] 압축.
- `section_writer` — 단일 월이 아닌 **복수 월 데이터** (`evidence_periods`) 횡단 집필.
- `assembler` (구 `compiler`) — 2개 섹션 고정 조립.
- `translate` / `polish` — 단일 LLM 호출 (~800단어). 문단 분할·완전성 검증 제거.

**제거:**
- 기획 루프 (draft_planner, planner_critique, route_outline)
- theme_analyzer (strategic_analyst에 흡수)
- prepare_translation_node, 복잡 번역 헬퍼 (문단 분할, 완전성 검증, 소스 폴백)
- resume 기능 (임시 제거)
- v2 유틸 다수 (split_compiled_by_section, validate_outline_periods 등)

---

## §2. 파일 지도

```
├── main.py                  실행 진입점 (135줄)
├── requirements.txt         openai, langgraph, pydantic, python-dotenv, python-docx
├── .env.example             OPENAI_BASE_URL / OPENAI_MODEL / LLM_MAX_RPM
├── pipeline_error.log       노드 단위 실패 로그 (자동 생성, append)
├── data/records.jsonl       입력 JSONL (gen_dummy.py로 생성)
├── scripts/
│   ├── gen_dummy.py         더미 JSONL 15건 생성기 (81줄)
│   └── md_to_docx.py       마크다운 → DOCX 변환 (235줄, 독립 스크립트)
└── src/
    ├── prompt_config.py     ★ 사용자 커스텀 프롬프트 설정 (285줄)
    ├── schemas.py           Pydantic 스키마 7종 (56줄)
    ├── state.py             GraphState (46줄)
    ├── context_guard.py     95KB 예산 관리 (164줄)
    ├── llm.py               OpenAI SDK + Rate Limiter + 504 방어 (469줄)
    ├── logger.py            타임라인 로거 + 에러 로그 (115줄)
    ├── utils.py             Pure Python 결정론 로직 (135줄)
    ├── nodes.py             16개 노드 + 4개 라우터 (1011줄)
    ├── graph.py             LangGraph 조립 (80줄)
    ├── artifacts.py         중간 산출물 저장 (76줄)
    └── docx_builder.py      전문 DOCX 빌더 (433줄)
```

---

## §3. 전체 그래프 구조

```
START → load_docs → [fanout] strict_extractor(×N) → chrono_sorter     Phase 1: 추출
     → [fanout] period_digest(×M) → strategic_analyst                 Phase 2+3: 압축·전략
     → init_writing → section_writer ⟲ fact_checker                   Phase 4: 집필 루프
     → assembler → implications_writer → polish → translate            Phase 4b+5: 시사점·윤문·번역
     → docx_builder → END                                             Phase 6: DOCX 생성
```

**검증 루프:** 집필(section) 1곳만 유지. 나머지는 직선.
**섹션 수:** 고정 2개 (blueprint.section_1, section_2).

---

## §4. 핵심 방어 기제

| # | 위험 | 방어 |
|---|------|------|
| 1 | Fact-checker 회귀 | `previous_draft` + `hallucinated_tokens` 블랙리스트 주입 |
| 2 | Fail-Safe 강제통과 | ⚠️ 워터마크 삽입 |
| 3 | 504 타임아웃 | 국부 감축 (-5KB/step) + reasoning 다운그레이드 + 노드 재실행 |
| 4 | 95KB 초과 방지 | `effective_budget()` 전역 참조 + 분할/압축 |
| 5 | 고유명사 보존 | `extract_proper_nouns` → 번역 프롬프트에 목록 주입 |
| 6 | 에러 추적 | `pipeline_error.log` (타임스탬프·노드·스택트레이스) |

---

## §5. 절대 준수 사항

1. LangChain LLM 래퍼 사용 금지 — 오직 `openai.OpenAI()` 직접 사용
2. 모든 LLM 응답은 Pydantic 강제 — `structured_call()` → `extract_json()` → `model_validate()`
3. `response_format` 인자 사용 금지 — 프롬프트 가드 + 3단 파서
4. Pure Python 영역에 LLM 호출 금지 — `utils.py`, `chrono_sorter`, `assembler`
5. 영어 출력 노드는 `_EN_ENFORCE` 필수, 번역 노드는 미사용
6. **user 메시지 절단 금지** — 504는 노드 재실행(분할 로직 재생성)으로 처리
7. **504 감축은 국부적** — 실패 노드만 축소, 성공 후 원복
8. **API 요청 95KB 이하** — `effective_budget()` + `available_data_budget(budget_override=)` 준수

---

## §6. 프롬프트 커스텀 (사용자 편집)

`src/prompt_config.py` 파일만 편집하면 백서의 톤/목적/편향을 조정할 수 있습니다.

| 설정 | 기본값 | 적용 단계 |
|------|--------|----------|
| `DOCUMENT_PURPOSE` | "가독성이 뛰어난 기간별 이벤트 기반 백서" | 압축·전략·집필·번역 |
| `TONE_DIRECTIVE` | "" (중립 객관) | 압축·집필·번역 |
| `TARGET_AUDIENCE` | "" (일반 독자) | 전략·집필·번역 |
| `CUSTOM_DIRECTIVES` | "" | 집필만 |
| `DOCUMENT_TITLE` | "" (LLM 자동 생성) | DOCX 표지 |
| `DOCUMENT_SUBTITLE` | "" (자동 날짜 범위) | DOCX 표지 |
| `ORGANIZATION_NAME` | "" | DOCX 표지 |
| `DOMAIN_KNOWLEDGE` | "" | 시사점 생성 (도메인 배경지식) |
| `IMPLICATIONS_DIRECTIVE` | "" | 시사점 생성 (시사점 작성 지시) |
| `TARGET_WORDS_PER_SECTION` | 400 | 전략·집필 |

**커스텀 예시 (투자자 보고서):**
```python
DOCUMENT_PURPOSE = "투자자 대상 성장 스토리 백서"
TONE_DIRECTIVE = "긍정적 성과와 성장세를 강조하되, 사실에 기반할 것"
TARGET_AUDIENCE = "C-레벨 경영진 — 핵심 수치와 의사결정 포인트 중심"
CUSTOM_DIRECTIVES = "매 섹션 말미에 '시사점' 문단 추가"
DOCUMENT_TITLE = "2026 성장 전략 보고서"
ORGANIZATION_NAME = "ABC Corporation"
```

**⚠️ 안전장치:** 편향 설정과 무관하게 `fact_checker`가 원본 데이터 외 사실 추가를 여전히 차단합니다.

---

## §7. 실행 방법

```bash
# 환경 셋업
pip install -r requirements.txt
cp .env.example .env  # OPENAI_BASE_URL, OPENAI_MODEL 편집

# 데이터 생성
python -m scripts.gen_dummy

# 백서 + DOCX 생성
python -m main                              # reasoning=high, 팩트체크 ON (기본)
python -m main --reasoning medium           # 서버 타임아웃 회피 우선
python -m main --skip-fact-check            # 팩트체크/환각검증 생략 (빠른 실행)
python -m main --output report.md           # 한글 백서 추가 저장
python -m main --output-docx report.docx    # DOCX 추가 저장
```

**산출물 디렉토리:**
```
output/YYYYMMDD_HHMMSS/
├── phase1_extracted_events.json
├── phase1_grouped_chunks.json
├── phase3_blueprint.json
├── phase4_sections/section_00.md, section_01.md
├── phase4_compiled_en.md
├── phase4_implications_en.md
├── phase4_polished_en.md
├── phase5_output_kr.md    (한글 백서)
├── phase5_output_en.md    (영문 원본)
└── output.docx            (전문 DOCX)
```

---

## §8. 다음 AI Agent 시나리오

### A: "실행 결과 이상하다"
→ `pipeline_error.log` + `[fact_checker]`, `[section_writer]`, `[504_retry]` 로그 확인.
→ `output/*/phase3_blueprint.json`으로 strategic_analyst의 섹션 배분 점검.

### B: "200건 실제 데이터"
→ `.env` 모델 분리, `LLM_MAX_RPM` 조정, `--reasoning medium` 권장.
→ 추출(N건) + 다이제스트(M월) fanout이 주요 LLM 소비처.

### C: "DOCX 스타일 변경"
→ `src/docx_builder.py` 수정. 색상 상수(`NAVY`, `GRAY_SUBTITLE`), 폰트(`FONT_PRIMARY`), 마진 등.

### D: "번역 스타일 변경"
→ `src/nodes.py` `translate_node()` 시스템 프롬프트 수정.

### E: "섹션 수 변경 (3개 이상)"
→ `schemas.py` `DocumentBlueprint`에 section_3 추가 + `strategic_analyst_node` 프롬프트 + `route_next_section` 임계값 + `assembler_node` 범위 변경. 연쇄 수정 주의.

### F: "검증 루프 복원"
→ git history `09fda16` (v2.0) 참조. `final_fact_checker`, `translation_checker` 코드 복원 가능.

### G: "resume 기능 복원"
→ git history `06ff639` 참조. `build_resume_graph()`, `load_run_state()` 복원 + v3 그래프 구조에 맞게 수정 필요.

### H: "시사점 스타일/내용 변경"
→ `src/prompt_config.py`의 `IMPLICATIONS_DIRECTIVE`, `DOMAIN_KNOWLEDGE` 편집.
→ 시사점 생성 자체를 제거하려면 `implications_writer_node` 바이패스 + `docx_builder` 시사점 섹션 제거 필요.

---

## §9. 디버깅 체크리스트

| 증상 | 확인 |
|------|------|
| 504 반복 | `--reasoning medium` 또는 `LLM_CONTEXT_BUDGET_KB` 하향 |
| JSON 파싱 실패 | `[structured_call] retry` 로그, extract_json 단계 확인 |
| 고유명사 누락 | `extract_proper_nouns` 출력 점검, 패턴 추가 |
| 번역 톤 불일치 | `translate_node()` 시스템 프롬프트 스타일 가이드 조정 |
| 빈 final_output | `[assembler] sections=0` → save_section 동작 점검 |
| 노드 실패 추적 | `pipeline_error.log` 확인 (타임스탬프·노드명·스택트레이스) |
| DOCX 렌더링 깨짐 | `docx_builder.py` 마크다운 파서 (`_render_markdown`) 점검 |
| evidence_periods 비어있음 | `phase3_blueprint.json` 확인 → 프롬프트의 available periods 목록 점검 |

---

## §10. 첫 5분 체크리스트

- [ ] 이 문서 §0~§4 읽기
- [ ] `src/nodes.py` Phase 주석 + 라우터 함수 훑기
- [ ] `src/graph.py` 그래프 조립 구조 확인
- [ ] `git log --oneline` 히스토리 확인
- [ ] 사용자 첫 메시지 → 시나리오 A/B/C/D/E/F/G 분류
- [ ] 코드 수정 시 AST 검증 후 커밋
