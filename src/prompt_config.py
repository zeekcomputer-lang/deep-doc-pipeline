"""
프롬프트 커스텀 설정 — 사용자 편집 전용 파일.
═══════════════════════════════════════════════

이 파일의 값을 수정하여 백서의 톤, 목적, 편향을 조정할 수 있습니다.
수정 후 파이프라인을 다시 실행하면 즉시 반영됩니다.
코드 수정 없이 이 파일만 편집하면 됩니다.

기본값: "가독성이 뛰어난 기간별 이벤트 기반 백서" (중립 객관 톤)

적용 범위:
  - 추출 단계 (strict_extractor): DOMAIN
  - 압축 단계 (period_digest): PURPOSE + TONE + DOMAIN
  - 전략 분석 (strategic_analyst): PURPOSE + AUDIENCE + DOMAIN
  - 집필 단계 (section_writer): PURPOSE + TONE + AUDIENCE + CUSTOM + DOMAIN
  - 시사점 단계 (implications_writer): DOMAIN + IMPLICATIONS
  - 번역 단계 (translate): PURPOSE + TONE + AUDIENCE + DOMAIN (한국어)
  - 윤문 단계 (polish): 적용하지 않음 (사실 변경 금지 원칙)
"""
from __future__ import annotations


# ════════════════════════════════════════════════════════════════
# 1. 문서 목적 (Document Purpose)
# ════════════════════════════════════════════════════════════════
# 백서의 전반적인 목적을 정의합니다.
# 이 값은 압축·전략·집필·번역 전 단계의 프롬프트에 주입됩니다.
#
# 커스텀 예시:
#   "경영진 보고용 성과 중심 요약 보고서"
#   "투자자 대상 긍정적 성장 스토리 백서"
#   "리스크 중심의 내부 감사 보고서"
#   "기술 실무자를 위한 상세 프로젝트 이력 문서"
#   "분기별 프로젝트 현황 대시보드 보고서"
#
DOCUMENT_PURPOSE: str = "가독성이 뛰어난 기간별 이벤트 기반 백서"


# ════════════════════════════════════════════════════════════════
# 2. 톤 및 편향 (Tone & Bias Directive)
# ════════════════════════════════════════════════════════════════
# 서술 톤과 사실 해석의 방향성을 지정합니다.
# 비워두면("") 중립 객관 톤으로 작성됩니다.
#
# ⚠️ 편향 설정은 사실을 왜곡하지 않습니다.
#    LLM에게 "강조 방향"을 지시할 뿐, fact_checker가
#    원본 데이터에 없는 사실 추가를 여전히 차단합니다.
#
# 커스텀 예시:
#   "긍정적 성과와 성장세를 강조하되, 사실에 기반할 것"
#   "리스크와 미해결 과제를 우선적으로 부각할 것"
#   "균형 잡힌 시각으로 성과와 과제를 동등하게 다룰 것"
#   "보수적 관점에서 확인된 사실만 서술할 것"
#
TONE_DIRECTIVE: str = ""


# ════════════════════════════════════════════════════════════════
# 3. 대상 독자 (Target Audience)
# ════════════════════════════════════════════════════════════════
# 문서의 주요 독자층을 지정합니다.
# 비워두면("") 일반 독자 대상으로 작성됩니다.
#
# 커스텀 예시:
#   "C-레벨 경영진 — 핵심 수치와 의사결정 포인트 중심"
#   "기술 실무자 — 상세 기술 내역과 구현 과정 포함"
#   "외부 투자자/파트너 — 비즈니스 임팩트와 성장 지표 중심"
#
TARGET_AUDIENCE: str = ""


# ════════════════════════════════════════════════════════════════
# 4. 추가 사용자 지시 (Custom Directives)
# ════════════════════════════════════════════════════════════════
# 프롬프트에 추가로 삽입할 자유 형식 텍스트입니다.
# 여러 줄 가능. 비워두면("") 무시됩니다.
#
# ⚠️ 집필(section_writer) 단계에만 주입됩니다.
#
# 커스텀 예시:
#   "매 섹션 말미에 '시사점' 문단을 추가할 것"
#   "수치 데이터는 반드시 표(table) 형태로 정리할 것"
#   "기술 약어 첫 등장 시 풀네임을 병기할 것"
#
CUSTOM_DIRECTIVES: str = ""


# ════════════════════════════════════════════════════════════════
# 5. 사전 지식 주입 (Domain Knowledge Injection)
# ════════════════════════════════════════════════════════════════
# LLM이 알지 못하는 용어, 프로젝트 맥락, 조직 구조 등을 사전에 주입합니다.
# 추출·압축·집필·번역 전 단계의 시스템 프롬프트에 주입됩니다.
# 비워두면("") 무시됩니다.
#
# 용도:
#   - 프로젝트별 용어 정의 ("PoC = 개념 검증, MVP = 최소 기능 제품")
#   - 프로젝트 단계 정의 ("기획 → PoC → MVP → 고도화 → 안정화")
#   - 조직/도메인 맥락 ("AI 전환 초기 단계의 제조업 기업")
#   - LLM 어텐션 집중 지시 ("비용 절감 효과와 ROI에 주목할 것")
#   - 약어·코드명 해설 ("Project Gemini = 차세대 ERP 전환 프로젝트")
#
# 커스텀 예시:
#   DOMAIN_KNOWLEDGE = """
#   - "gpt-oss" = 사내 자체 운영 LLM 서비스 (비공개)
#   - "PoC" = Proof of Concept (개념 검증)
#   - 프로젝트 단계: 기획 → PoC → MVP → 고도화 → 운영
#   - 현재 조직은 AI 전환 초기 단계 (도입 1년차)
#   - 핵심 관심사: 비용 대비 효과, 보안, 기존 시스템 연동
#   - "Blue Team" = 인프라·보안 담당 조직
#   """
#
DOMAIN_KNOWLEDGE: str = ""


# ════════════════════════════════════════════════════════════════
# 6. 시사점 작성 지시 (Implications Directive)
# ════════════════════════════════════════════════════════════════
# 시사점(Implications) 섹션 작성 시 LLM에게 추가 지시를 제공합니다.
# 비워두면("") 기본 시사점 프롬프트만 사용됩니다.
#
# 커스텀 예시:
#   "ROI 관점의 정량적 시사점을 우선 도출할 것"
#   "후속 프로젝트 제안을 포함할 것"
#   "리스크 요인과 대응 방안을 반드시 포함할 것"
#   "경쟁사 대비 포지셔닝 관점에서 서술할 것"
#
IMPLICATIONS_DIRECTIVE: str = ""


# ════════════════════════════════════════════════════════════════
# 7. 문서 메타 설정 (v3 Document Settings)
# ════════════════════════════════════════════════════════════════
DOCUMENT_TITLE: str = ""           # 표지 제목 (비워두면 LLM이 영문 생성 → 한글 자동 번역)
DOCUMENT_SUBTITLE: str = ""        # 부제 (비워두면 데이터 기간으로 자동 생성)
ORGANIZATION_NAME: str = ""        # 작성 조직명
TARGET_WORDS_PER_SECTION: int = 400  # 섹션당 목표 단어 수 (~1 page)


# ════════════════════════════════════════════════════════════════
# 내부 헬퍼 — 아래 함수들은 수정하지 마십시오.
# ════════════════════════════════════════════════════════════════

def _build_context_block(
    include_purpose: bool = True,
    include_tone: bool = True,
    include_audience: bool = True,
    include_custom: bool = False,
    include_domain: bool = True,
    language: str = "en",
) -> str:
    """노드 프롬프트에 주입할 사용자 컨텍스트 블록 생성.

    모든 항목이 비어있으면 빈 문자열을 반환하여
    기존 프롬프트에 영향을 주지 않습니다.
    """
    parts: list[str] = []

    if include_domain and DOMAIN_KNOWLEDGE:
        label = "Domain Context" if language == "en" else "사전 지식"
        parts.append(f"[{label}]\n{DOMAIN_KNOWLEDGE.strip()}")

    if include_purpose and DOCUMENT_PURPOSE:
        label = "Document Purpose" if language == "en" else "문서 목적"
        parts.append(f"[{label}] {DOCUMENT_PURPOSE}")

    if include_tone and TONE_DIRECTIVE:
        if language == "en":
            parts.append(
                f"[Tone Directive] {TONE_DIRECTIVE} "
                "(This guides emphasis direction only. Do NOT fabricate facts.)"
            )
        else:
            parts.append(
                f"[톤 지시] {TONE_DIRECTIVE} "
                "(강조 방향 지시일 뿐, 사실 왜곡은 금지입니다.)"
            )

    if include_audience and TARGET_AUDIENCE:
        label = "Target Audience" if language == "en" else "대상 독자"
        parts.append(f"[{label}] {TARGET_AUDIENCE}")

    if include_custom and CUSTOM_DIRECTIVES:
        label = "Additional Directives" if language == "en" else "추가 지시"
        parts.append(f"[{label}]\n{CUSTOM_DIRECTIVES}")

    if not parts:
        return ""

    header = "User Context" if language == "en" else "사용자 컨텍스트"
    return f"\n\n[{header}]\n" + "\n".join(parts) + "\n"


# ── 단계별 컨텍스트 조회 함수 ──────────────────────────────────

def get_domain_context(language: str = "en") -> str:
    """도메인 지식만 단독으로 주입 (extractor 등 최소 프롬프트 노드용)."""
    if not DOMAIN_KNOWLEDGE:
        return ""
    label = "Domain Context" if language == "en" else "사전 지식"
    return f"\n\n[{label}]\n{DOMAIN_KNOWLEDGE.strip()}\n"


def get_summary_context() -> str:
    """압축 단계용 (period_digest).

    PURPOSE + TONE + DOMAIN 주입. AUDIENCE/CUSTOM은 압축에 불필요.
    """
    return _build_context_block(
        include_purpose=True, include_tone=True,
        include_audience=False, include_custom=False,
        include_domain=True,
        language="en",
    )


def get_planning_context() -> str:
    """기획 단계용 (미사용, v3에서 strategic_analyst로 대체).

    PURPOSE + AUDIENCE + DOMAIN. TONE은 기획에서 불필요.
    """
    return _build_context_block(
        include_purpose=True, include_tone=False,
        include_audience=True, include_custom=False,
        include_domain=True,
        language="en",
    )


def get_writing_context() -> str:
    """집필 단계용 (section_writer).

    전체 항목 주입. 실제 본문 작성에 모든 설정 반영.
    """
    return _build_context_block(
        include_purpose=True, include_tone=True,
        include_audience=True, include_custom=True,
        include_domain=True,
        language="en",
    )


def get_strategic_context() -> str:
    """전략 분석 단계용 (strategic_analyst). PURPOSE + AUDIENCE + DOMAIN."""
    return _build_context_block(
        include_purpose=True, include_tone=False,
        include_audience=True, include_custom=False,
        include_domain=True,
        language="en",
    )


def get_implications_context() -> str:
    """시사점 단계용 (implications_writer). DOMAIN + IMPLICATIONS."""
    parts: list[str] = []
    if DOMAIN_KNOWLEDGE:
        parts.append(f"[Domain Context]\n{DOMAIN_KNOWLEDGE.strip()}")
    if IMPLICATIONS_DIRECTIVE:
        parts.append(f"[Implications Directive] {IMPLICATIONS_DIRECTIVE}")
    if not parts:
        return ""
    return "\n\n[User Context]\n" + "\n".join(parts) + "\n"


def get_docx_meta() -> dict:
    """DOCX 빌더에 전달할 메타데이터."""
    return {
        "title": DOCUMENT_TITLE,
        "subtitle": DOCUMENT_SUBTITLE,
        "organization": ORGANIZATION_NAME,
        "target_words": TARGET_WORDS_PER_SECTION,
    }


def get_translation_context() -> str:
    """번역 단계용 (translate).

    PURPOSE + TONE + AUDIENCE + DOMAIN을 한국어로 주입.
    CUSTOM은 번역 단계에서 제외 (원문 충실 번역 원칙).
    """
    return _build_context_block(
        include_purpose=True, include_tone=True,
        include_audience=True, include_custom=False,
        include_domain=True,
        language="ko",
    )
