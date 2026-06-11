# Deep Doc Pipeline — 설계 명세서 v3.1

> **버전:** v3.1
> **최종 갱신:** 2026-06-11
> **목적:** JSONL 문서 → 영문 분석·집필 → 한글 백서 + 전문 DOCX 자동 생성 LangGraph 파이프라인
> **GitHub:** https://github.com/zeekcomputer-lang/deep-doc-pipeline

---

## 1. 아키텍처 설계 철학

### 1.1 저성능 LLM 방어 4대 원칙

| # | 원칙 | 설명 |
|---|------|------|
| 1 | **극단적 마이크로 태스킹** | One Node = One Task. 요약·기획·집필·검수를 하나의 프롬프트에 섞지 않음 |
| 2 | **계층적 컨텍스트 압축** | 200건을 한 번에 주지 않음. 월별 다이제스트 → 전략 분석 → 섹션별 데이터 주입 |
| 3 | **자가 검증 루프** | 집필 직후 팩트체커가 원본 데이터와 대조. 환각 발견 시 재작성 강제 |
| 4 | **결정론적 로직 우선** | 날짜 정렬·기간 필터링·문서 조립은 Pure Python. LLM에 맡기지 않음 |

### 1.2 v3.1 설계 원칙

- **EN-only LLM**: Phase 1~4 전체를 영어로 수행. 한국어는 Phase 5 번역에서만 등장.
- **95KB 하드리밋**: 모든 API 호출의 메시지 페이로드가 95KB 미만. 초과 시 분할·압축.
- **504 국부 감축**: 타임아웃 시 실패 노드만 축소, 성공 후 원복. 전역 품질 저하 방지.
- **user 메시지 불변**: LLM에 전달되는 데이터는 절대 절단하지 않음. 분할로만 해결.
- **3-page DOCX 아키텍처**: 표지(1p) + 본문 연속 흐름(페이지 분리 없음) + 시사점 섹션. 고정 구조로 LLM 호출 최소화.
- **내러티브 기반 구조**: 월별 섹션이 아닌 테마 기반 2개 내러티브. 복수 월 횡단 분석.
- **시사점 자동 생성**: `implications_writer`가 블루프린트 기반 시사점 섹션을 자동 생성.

---

## 2. 절대 준수 제약 사항

1. **순수 OpenAI SDK** — `openai.OpenAI()` 직접 사용. LangChain LLM 래퍼 금지.
2. **Pydantic 강제 출력** — `structured_call()` → `extract_json()` 3단 파서 → `model_validate()`.
3. **`response_format` 인자 금지** — GPT-OSS 호환을 위해 프롬프트 가드 + 파서로 JSON 강제.
4. **Pure Python 영역 분리** — `utils.py`, `chrono_sorter`, `assembler`에서 LLM 호출 금지.
5. **영어/한국어 분리** — `_EN_ENFORCE` 접미사로 Phase 1~4 영어 강제. 번역 노드는 미사용.

---

## 3. GraphState 스키마

```python
class GraphState(TypedDict, total=False):
    # Phase 1: Data Ingestion
    raw_docs: List[Dict[str, Any]]
    extracted_events: Annotated[List[Dict], operator.add]
    grouped_chunks: Dict[str, List[Dict]]

    # Phase 2: Compression
    period_digests: Annotated[Dict[str, str], update_dict]

    # Phase 3: Strategic Analysis
    blueprint: Dict

    # Phase 4: Writing Loop
    current_section_index: int
    current_draft: str
    previous_draft: str
    hallucinated_tokens: Annotated[List[str], operator.add]
    draft_feedback: str
    is_draft_approved: bool
    section_retry_count: int
    completed_sections: Annotated[Dict[int, str], update_dict]

    # Phase 4b: Implications
    implications_text: str

    # Phase 5: Assembly + Polish + Translate
    final_compiled: str
    english_output: str
    final_output: str
    doc_title_kr: str
    doc_subtitle_kr: str

    # Phase 6: DOCX
    docx_path: str
```

---

## 4. Pydantic 응답 스키마 (7종)

| 스키마 | 용도 | 핵심 필드 |
|--------|------|----------|
| `ExtractedEvent` | 문서 1건 추출 | date, issue, action |
| `PeriodDigest` | 월별 1~2문장 다이제스트 | digest, event_count, key_metrics[] |
| `SectionPlan` | 섹션 설계 (Blueprint 내부) | title, narrative, evidence_periods[], key_points[] |
| `DocumentBlueprint` | 문서 청사진 | doc_title, doc_subtitle, section_1, section_2, implications_points[] |
| `SectionDraft` | 섹션 본문 | content |
| `FactCheckResult` | 팩트체크 | is_draft_approved, feedback, hallucinated_terms[] |
| `PolishedDocument` | 윤문/번역 출력 | content |

---

## 5. 그래프 구조 — 16 노드 + 4 라우터

```
START
  │
  ▼
load_docs → [fanout] strict_extractor(×N) → chrono_sorter         Phase 1: 추출
  │
  ▼
[fanout] period_digest(×M) → strategic_analyst                    Phase 2+3: 압축·전략
  │
  ▼
init_writing → section_writer ⟲ fact_checker                      Phase 4: 집필 루프
  │              │                                                  (--skip-fact-check: 자동 승인)
  │              ├─ retry_section (재작성, 최대 3회)
  │              ├─ save_section (승인)
  │              └─ save_section_with_warning (강제통과 + ⚠️ 워터마크)
  │
  ▼
assembler (Pure Python) → implications_writer → polish → translate  Phase 4b+5: 시사점·조립·윤문·번역
  │
  ▼
docx_builder → END                                                Phase 6: DOCX 생성
  │
  ▼
output/YYYYMMDD_HHMMSS/output.docx + phase5_output_kr.md + phase5_output_en.md
```

**검증 루프:** 집필(section) 1곳만 유지. 윤문·번역·DOCX는 직선.

---

## 6. 노드별 설계

### Phase 1: 추출

| 노드 | 유형 | 설명 |
|------|------|------|
| `load_docs` | Python | `data/records.jsonl` 로드. 파싱 실패 건 skip. |
| `strict_extractor` | LLM ×N | 문서 1건 → `ExtractedEvent` 추출. 95KB 초과 시 절단. |
| `chrono_sorter` | Python | 날짜순 정렬 + `YYYY-MM` 월별 그루핑. |

### Phase 2: 압축

| 노드 | 유형 | 설명 |
|------|------|------|
| `period_digest` | LLM ×M | 월별 이벤트 → 1~2문장 다이제스트 + 이벤트 수 + KPI. 예산 초과 시 배치 분할→병합. |

### Phase 3: 전략 분석

| 노드 | 유형 | 설명 |
|------|------|------|
| `strategic_analyst` | LLM | 전체 다이제스트 → `DocumentBlueprint` 생성. 2개 테마 내러티브 + 각 섹션의 `evidence_periods` 지정. |

### Phase 4: 집필 + 조립 + 윤문

| 노드 | 유형 | 설명 |
|------|------|------|
| `init_writing` | Python | 커서 초기화 (index=0, retry=0). |
| `section_writer` | LLM | `evidence_periods`의 **복수 월** 원본 이벤트를 주입하여 내러티브 본문 집필 (~400단어). 재작성 시 `previous_draft` + `hallucinated_tokens` 블랙리스트 주입. 예산 초과 시 배치 분할→병합. |
| `fact_checker` | LLM | 초안 vs 원본 이벤트 대조. 환각 토큰 추출 필수. `--skip-fact-check` 시 자동 승인. 예산 초과 시 배치 분할 + `cross_check_terms()` 교차 검증. |
| `retry_section` | Python | `previous_draft` 저장 + retry 카운터 증가. |
| `save_section` | Python | 승인된 섹션 저장 + 인덱스 진행. |
| `save_section_with_warning` | Python | 3회 실패 시 ⚠️ 워터마크 + 강제 저장. |
| `assembler` | Python | 목차 순서대로 2개 섹션 조립. **LLM 호출 금지.** |

### Phase 4b: 시사점

| 노드 | 유형 | 설명 |
|------|------|------|
| `implications_writer` | LLM | 블루프린트의 `implications_points`를 기반으로 시사점 섹션 자동 생성. `IMPLICATIONS_DIRECTIVE` 프롬프트 반영. |

### Phase 5: 윤문 + 번역

| 노드 | 유형 | 설명 |
|------|------|------|
| `polish` | LLM | 단일 LLM 호출 (~800단어). 사실 변경/추가 금지. 예산 초과 시 윤문 생략. |
| `translate` | LLM | 단일 LLM 호출 (~800단어). 충실 번역 + 고유명사 보존 (`extract_proper_nouns`). 예산 초과 시 영문 원본 유지. 제목/부제 한글 번역 포함 (`[제목]`/`[부제]` 추출). |

### Phase 6: DOCX 생성

| 노드 | 유형 | 설명 |
|------|------|------|
| `docx_builder` | Python | `DocxBuilder` 클래스로 전문 DOCX 생성: 표지(네이비 타이틀+악센트 라인+날짜+조직명) + 본문 2페이지(네이비 헤딩+마크다운 렌더링) + 헤더/푸터/페이지 번호. |

### 라우터

| 라우터 | 입력 | 분기 |
|--------|------|------|
| `fanout_to_extractor` | load_docs 후 | 문서 1건당 Send("strict_extractor") |
| `fanout_to_period_digest` | chrono_sorter 후 | 월별 Send("period_digest") |
| `route_section_draft` | fact_checker 후 | save_section / retry_section / save_section_with_warning |
| `route_next_section` | save 후 | section_writer (다음 섹션) / assembler (완료) |

---

## 7. 방어 기제

| # | 위험 | 방어 | 구현 위치 |
|---|------|------|----------|
| 1 | Fact-checker 회귀 | `previous_draft` + `hallucinated_tokens` 블랙리스트 | `section_writer` |
| 2 | Fail-Safe 강제통과 | ⚠️ 워터마크 삽입 | `save_section_with_warning` |
| 3 | 504 타임아웃 | 국부 감축 (-5KB/step) + reasoning 다운그레이드 + 노드 재실행 | `@retry_on_504` / `llm.py` |
| 4 | 95KB 초과 | `effective_budget()` 사전 측정 + 분할/압축 | `context_guard.py` / 각 노드 |
| 5 | 고유명사 소실 | `extract_proper_nouns` → 번역 프롬프트에 목록 주입 | `utils.py` / `translate` |
| 6 | 에러 추적 | `pipeline_error.log` (타임스탬프·노드·스택트레이스) | `logger.py` / `main.py` |

---

## 8. 실행 옵션

```bash
python -m main                              # 기본 (reasoning=high, 팩트체크 ON)
python -m main --reasoning medium           # 서버 타임아웃 회피 우선
python -m main --skip-fact-check            # 팩트체크/환각검증 생략 (빠른 실행)
python -m main --output report.md           # 한글 백서 추가 저장
python -m main --output-docx report.docx    # DOCX 추가 저장
```

**환경변수:**

| 변수 | 기본값 | 설명 |
|------|-------|------|
| `OPENAI_BASE_URL` | — | LLM 엔드포인트 |
| `OPENAI_MODEL` | gpt-oss-20b | 기본 모델 |
| `EXTRACTOR_MODEL` / `JUDGE_MODEL` / `WRITER_MODEL` | (OPENAI_MODEL) | 역할별 모델 분리 |
| `LLM_MAX_RPM` | 12 | 분당 최대 호출 |
| `LLM_MAX_CONCURRENT` | 5 | 동시 호출 상한 |
| `LLM_CONTEXT_BUDGET_KB` | 95 | per-call 컨텍스트 예산 (KB) |

---

## 9. 프롬프트 커스텀

`src/prompt_config.py` 파일을 편집하여 백서의 톤·목적·편향을 조정할 수 있습니다.

| 설정 | 기본값 | 적용 단계 |
|------|--------|----------|
| `DOCUMENT_PURPOSE` | "가독성이 뛰어난 기간별 이벤트 기반 백서" | 압축·전략·집필·번역 |
| `TONE_DIRECTIVE` | "" (중립 객관) | 압축·집필·번역 |
| `TARGET_AUDIENCE` | "" (일반 독자) | 전략·집필·번역 |
| `CUSTOM_DIRECTIVES` | "" | 집필만 |
| `DOMAIN_KNOWLEDGE` | "" | 시사점 생성 (도메인 배경지식) |
| `IMPLICATIONS_DIRECTIVE` | "" | 시사점 생성 (시사점 작성 지시) |
| `DOCUMENT_TITLE` | "" (LLM 자동 생성) | DOCX 표지 |
| `DOCUMENT_SUBTITLE` | "" (자동 날짜 범위) | DOCX 표지 |
| `ORGANIZATION_NAME` | "" | DOCX 표지 |
| `TARGET_WORDS_PER_SECTION` | 400 | 전략·집필 |

**안전장치:** 편향 설정과 무관하게 `fact_checker`가 원본 데이터 외 사실 추가를 차단합니다.

---

## 10. DOCX 빌더

`src/docx_builder.py`의 `DocxBuilder` 클래스가 전문 DOCX를 직접 생성합니다.

### DOCX 구조

| 구성 | 설명 |
|------|------|
| **표지** | 네이비(#1B365D) 한글 타이틀(28pt) + 악센트 라인 + 한글 서브타이틀(14pt) + 날짜 + 조직명 |
| **본문** | 섹션 1·2 연속 흐름 (페이지 분리 없음). 네이비 헤딩(16pt 볼드) + 마크다운 본문 렌더링 |
| **시사점** | `implications_writer` 생성 시사점 섹션. 본문 이후 연속 배치 |

### 마크다운 렌더링

지원: `##`/`###` 헤딩, `-`/`*` 불릿 리스트, `**bold**`, `` `code` ``

### 스타일

- 본문: 맑은 고딕 10.5pt, #333333, 행간 1.15
- 헤더: 문서 제목 (8pt, 우측 정렬)
- 푸터: 자동 페이지 번호 (중앙)
- 표지: `titlePg` 속성으로 헤더/푸터 미표시
- 마진: 2.54cm 전면

---

## 11. 파일 구조

```
├── main.py                  실행 진입점 (135줄)
├── requirements.txt         openai, langgraph, pydantic, python-dotenv, python-docx
├── .env.example             환경변수 템플릿
├── pipeline_error.log       노드 실패 로그 (자동 생성, .gitignore)
├── data/records.jsonl       입력 JSONL
├── scripts/
│   ├── gen_dummy.py         더미 JSONL 15건 생성기 (81줄)
│   └── md_to_docx.py       마크다운 → DOCX 변환 (235줄, 독립 스크립트)
└── src/
    ├── prompt_config.py     ★ 사용자 커스텀 프롬프트 설정 (285줄)
    ├── schemas.py           Pydantic 응답 스키마 7종 (56줄)
    ├── state.py             GraphState + reducer (46줄)
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

## 12. 출력 구조

```bash
python -m main
# → output/YYYYMMDD_HHMMSS/
#    ├── phase1_extracted_events.json
#    ├── phase1_grouped_chunks.json
#    ├── phase3_blueprint.json
#    ├── phase4_sections/section_00.md, section_01.md
#    ├── phase4_compiled_en.md
#    ├── phase4_implications_en.md
#    ├── phase4_polished_en.md
#    ├── phase5_output_kr.md    (한글 백서)
#    ├── phase5_output_en.md    (영문 원본)
#    └── output.docx            (전문 DOCX)

# 추가 저장
python -m main --output report.md --output-docx report.docx
```

---

## 13. v2.0 → v3.0 → v3.1 변경 요약

| 항목 | v2.0 | v3.0 | v3.1 |
|------|------|------|------|
| 구조 | 월별 N개 섹션 | 테마 기반 고정 2섹션 | 동일 + 시사점 섹션 추가 |
| 기획 | draft_planner ⟲ planner_critique (루프) | strategic_analyst (단일 노드) | 동일 + implications_points 생성 |
| 요약 | period_summarizer (3문장) | period_digest (1~2문장 + KPI) | 동일 |
| 테마 | theme_analyzer (별도 노드) | strategic_analyst에 흡수 | 동일 |
| 집필 | 단일 월 데이터 | 복수 월 데이터 (evidence_periods) | 동일 |
| 시사점 | — | — | implications_writer 노드 신설 |
| 조립 | compiler | assembler (2섹션 고정) | 동일 |
| 번역 | 섹션별 분할 + 완전성검증 + 문단분할 + 소스폴백 | 단일 호출 (~800단어) | 동일 + 제목/부제 한글 번역 |
| 윤문 | 섹션별 분할 | 단일 호출 (~800단어) | 동일 |
| 출력 | .md (한글) + .md (영문) | .md 2종 + .docx (전문) | 동일 + phase4_implications_en.md |
| DOCX | — | 표지 + 본문 2페이지 (섹션별 페이지 분리) | 표지 + 본문 연속 흐름 + 시사점 + 한글 제목 |
| LLM 호출 | ~60-160회 | ~20-30회 | ~20-30회 (+1 implications) |
| 노드 수 | 17 + 라우터 5 | 15 + 라우터 4 | 16 + 라우터 4 |
| 코드량 | 3,080줄 | 3,119줄 (-262 순감, docx_builder +435) | 3,321줄 (+202) |
| resume | 지원 (--resume) | 임시 제거 | 동일 |
