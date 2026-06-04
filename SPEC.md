# Deep Doc Pipeline — 설계 명세서 v2.0

> **버전:** v2.0
> **최종 갱신:** 2026-06-04
> **목적:** JSONL 문서 → 영문 분석·집필 → 한글 백서 자동 생성 LangGraph 파이프라인
> **GitHub:** https://github.com/zeekcomputer-lang/deep-doc-pipeline

---

## 1. 아키텍처 설계 철학

### 1.1 저성능 LLM 방어 4대 원칙

| # | 원칙 | 설명 |
|---|------|------|
| 1 | **극단적 마이크로 태스킹** | One Node = One Task. 요약·기획·집필·검수를 하나의 프롬프트에 섞지 않음 |
| 2 | **계층적 컨텍스트 압축** | 200건을 한 번에 주지 않음. 월별 요약 → 전체 테마 → 섹션별 데이터 주입 |
| 3 | **자가 검증 루프** | 기획·집필 직후 팩트체커가 원본 데이터와 대조. 환각 발견 시 재작성 강제 |
| 4 | **결정론적 로직 우선** | 날짜 정렬·기간 필터링·문서 조립은 Pure Python. LLM에 맡기지 않음 |

### 1.2 추가 설계 원칙 (v2.0)

- **EN-only LLM**: Phase 1~4 전체를 영어로 수행. 한국어는 Phase 5 번역에서만 등장.
- **95KB 하드리밋**: 모든 API 호출의 메시지 페이로드가 95KB 미만. 초과 시 분할·압축.
- **504 국부 감축**: 타임아웃 시 실패 노드만 축소, 성공 후 원복. 전역 품질 저하 방지.
- **user 메시지 불변**: LLM에 전달되는 데이터는 절대 절단하지 않음. 분할로만 해결.

---

## 2. 절대 준수 제약 사항

1. **순수 OpenAI SDK** — `openai.OpenAI()` 직접 사용. LangChain LLM 래퍼 금지.
2. **Pydantic 강제 출력** — `structured_call()` → `extract_json()` 3단 파서 → `model_validate()`.
3. **`response_format` 인자 금지** — GPT-OSS 호환을 위해 프롬프트 가드 + 파서로 JSON 강제.
4. **Pure Python 영역 분리** — `utils.py`, `chrono_sorter`, `compiler`에서 LLM 호출 금지.
5. **영어/한국어 분리** — `_EN_ENFORCE` 접미사로 Phase 1~4 영어 강제. 번역 노드는 미사용.

---

## 3. GraphState 스키마

```python
class GraphState(TypedDict, total=False):
    # Phase 1: 추출
    raw_docs: List[Dict[str, Any]]
    extracted_events: Annotated[List[Dict], operator.add]

    # Phase 2: 요약
    grouped_chunks: Dict[str, List[Dict]]       # "YYYY-MM" → events
    period_summaries: Annotated[Dict[str, str], update_dict]
    global_theme: str

    # Phase 3: 기획 루프
    outline: List[Dict]
    outline_feedback: str
    is_outline_approved: bool
    outline_retry_count: int

    # Phase 4: 집필 루프
    current_section_index: int
    current_draft: str
    previous_draft: str                          # 회귀 방지용 직전 반려 초안
    hallucinated_tokens: Annotated[List[str], operator.add]  # 환각 토큰 블랙리스트
    draft_feedback: str
    is_draft_approved: bool
    section_retry_count: int
    completed_sections: Annotated[Dict[int, str], update_dict]
    unverified_sections: Annotated[List[int], operator.add]  # Fail-Safe 감사 로그

    # Phase 4: 조립
    final_compiled: str
    final_output: str

    # Phase 5: 번역
    english_output: str
    proper_nouns: List[str]
```

---

## 4. Pydantic 응답 스키마 (7종)

| 스키마 | 용도 | 핵심 필드 |
|--------|------|----------|
| `ExtractedEvent` | 문서 1건 추출 | date, issue, action |
| `PeriodSummary` | 월별 3문장 요약 | summary |
| `GlobalTheme` | 전체 테마 1문단 | theme |
| `Outline` | 백서 목차 | items[{index, title, target_period, intent}] |
| `OutlineCritique` | 목차 검증 | is_outline_approved, feedback |
| `SectionDraft` | 섹션 본문 | content |
| `FactCheckResult` | 팩트체크 | is_draft_approved, feedback, hallucinated_terms[] |
| `PolishedDocument` | 윤문/번역 출력 | content |

---

## 5. 그래프 구조 — 17 노드 + 5 라우터

```
START
  │
  ▼
load_docs → [fanout] strict_extractor(×N) → chrono_sorter         Phase 1: 추출
  │
  ▼
[fanout] period_summarizer(×M) → theme_analyzer                   Phase 2: 요약
  │
  ▼
draft_planner ⟲ planner_critique                                  Phase 3: 기획 루프
  │ (승인)
  ▼
init_writing → section_writer ⟲ fact_checker                      Phase 4: 집필 루프
  │              │                                                  (--skip-fact-check: 자동 승인)
  │              ├─ retry_section (재작성, 최대 3회)
  │              ├─ save_section (승인)
  │              └─ save_section_with_warning (강제통과 + ⚠️ 워터마크)
  │
  ▼
compiler (Pure Python) → polish                                    Phase 4: 조립·윤문
  │
  ▼
prepare_translation → translate                                    Phase 5: 번역
  │
  ▼
END → output.md (한글) + output_en.md (영문)
```

---

## 6. 노드별 설계

### Phase 1: 추출

| 노드 | 유형 | 설명 |
|------|------|------|
| `load_docs` | Python | `data/records.jsonl` 로드. 파싱 실패 건 skip. |
| `strict_extractor` | LLM ×N | 문서 1건 → `ExtractedEvent` 추출. 95KB 초과 시 절단. |
| `chrono_sorter` | Python | 날짜순 정렬 + `YYYY-MM` 월별 그루핑. |

### Phase 2: 요약

| 노드 | 유형 | 설명 |
|------|------|------|
| `period_summarizer` | LLM ×M | 월별 이벤트 → 3문장 핵심 트렌드. 예산 초과 시 배치 분할→병합. |
| `theme_analyzer` | LLM | 전체 월간 요약 → 1문단 거시 인사이트. |

### Phase 3: 기획 루프

| 노드 | 유형 | 설명 |
|------|------|------|
| `draft_planner` | LLM | 테마 + 요약 → 목차(Outline) 생성. 각 항목에 `target_period` 배정. |
| `planner_critique` | Python + LLM | ① Python: target_period 존재 + 시계열 순서 검증. ② LLM: 구조적 합리성 평가. `--skip-fact-check` 시 ②만 생략. 최대 3회 후 강제 통과. |

### Phase 4: 집필 + 조립

| 노드 | 유형 | 설명 |
|------|------|------|
| `init_writing` | Python | 커서 초기화. |
| `section_writer` | LLM | `target_period` 원본 이벤트만 주입하여 섹션 본문 집필. 재작성 시 `previous_draft` + `hallucinated_tokens` 블랙리스트 주입. 예산 초과 시 이벤트 배치 분할→병합. |
| `fact_checker` | LLM | 초안 vs 원본 이벤트 대조. 환각 토큰 추출 필수. `--skip-fact-check` 시 자동 승인. |
| `save_section` | Python | 승인된 섹션 저장 + 인덱스 진행. |
| `save_section_with_warning` | Python | 3회 실패 시 ⚠️ 워터마크 + `unverified_sections` 기록 후 강제 저장. |
| `compiler` | Python | 목차 순서대로 섹션 조립. 감사 로그 첨부. LLM 호출 금지. |
| `polish` | LLM | 섹션별 윤문. 사실 변경/추가 금지. 예산 초과 시 문단별 분할. |

### Phase 5: 번역

| 노드 | 유형 | 설명 |
|------|------|------|
| `prepare_translation` | Python | 영문 백서 저장 + `extract_proper_nouns()` 고유명사 추출. |
| `translate` | LLM | 항상 섹션별 처리. 3단계 폴백: ① 전체 섹션 충실 번역 (ratio≥0.35 검증) → ② 문단별 8KB 분할 → ③ 소스 데이터로 한글 직접 생성. |

---

## 7. 방어 기제

| # | 위험 | 방어 | 구현 위치 |
|---|------|------|----------|
| 1 | Fact-checker 회귀 | `previous_draft` + `hallucinated_tokens` 블랙리스트 | `section_writer` |
| 2 | Fail-Safe 강제통과 | ⚠️ 워터마크 + `unverified_sections` 감사 로그 | `save_section_with_warning` |
| 3 | 504 타임아웃 | 국부 감축 (-5KB/step) + reasoning 다운그레이드 + 노드 재실행 | `@retry_on_504` / `llm.py` |
| 4 | 95KB 초과 | `effective_budget()` 사전 측정 + 분할/압축 | `context_guard.py` / 각 노드 |
| 5 | 고유명사 소실 | `extract_proper_nouns` → 번역 프롬프트에 목록 주입 | `utils.py` / `translate` |
| 6 | 번역 콘텐츠 소실 | 섹션별→문단별 분할 + 완전성 검증 + 소스데이터 폴백 | `translate` |
| 7 | 에러 추적 | `pipeline_error.log` (타임스탬프·노드·스택트레이스) | `logger.py` / `main.py` |

---

## 8. 실행 옵션

```bash
python -m main                         # 기본 (reasoning=high, 팩트체크 ON)
python -m main --reasoning medium      # 서버 타임아웃 회피 우선
python -m main --skip-fact-check       # 팩트체크/환각검증 생략 (빠른 실행)
python -m main --output report.md      # 출력 경로 지정
python -m main --output-en report_en.md  # 영문 원본 경로 지정
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
| `DOCUMENT_PURPOSE` | "가독성이 뛰어난 기간별 이벤트 기반 백서" | 요약·기획·집필·번역 |
| `TONE_DIRECTIVE` | "" (중립 객관) | 요약·집필·번역 |
| `TARGET_AUDIENCE` | "" (일반 독자) | 기획·집필·번역 |
| `CUSTOM_DIRECTIVES` | "" | 집필만 |

**안전장치:** 편향 설정과 무관하게 `fact_checker`가 원본 데이터 외 사실 추가를 차단합니다.

---

## 10. 파일 구조

```
├── main.py                  실행 진입점
├── requirements.txt         openai, langgraph, pydantic, python-dotenv, python-docx
├── .env.example             환경변수 템플릿
├── pipeline_error.log       노드 실패 로그 (자동 생성, .gitignore)
├── data/records.jsonl       입력 JSONL
├── scripts/
│   ├── gen_dummy.py         더미 JSONL 15건 생성기
│   └── md_to_docx.py       마크다운 → DOCX 변환
└── src/
    ├── prompt_config.py     ★ 사용자 커스텀 프롬프트 설정
    ├── schemas.py           Pydantic 응답 스키마 7종
    ├── state.py             GraphState + reducer
    ├── context_guard.py     95KB 예산 관리
    ├── llm.py               OpenAI SDK + Rate Limiter + 504 방어
    ├── logger.py            타임라인 로거 + 에러 로그
    ├── utils.py             Pure Python 결정론 로직
    ├── nodes.py             17개 노드 + 5개 라우터
    └── graph.py             LangGraph 조립
```

---

## 11. 출력 구조

```bash
python -m main --output report.md
# → report.md      한글 백서
# → report_en.md   영문 원본 (항상 생성)

# DOCX 변환 (개별 구동)
python scripts/md_to_docx.py report.md                     # → report.docx
python scripts/md_to_docx.py report.md report_en.md        # 한영 병합
```
