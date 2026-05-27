"""
Pydantic 스키마 정의.
모든 LLM 응답은 client.beta.chat.completions.parse + 이 스키마로 강제됨.
"""
from __future__ import annotations
from typing import List
from pydantic import BaseModel, Field


# ──────────────────────────────────────────────────────────────
# Phase 1: 정보 추출
# ──────────────────────────────────────────────────────────────
class ExtractedEvent(BaseModel):
    """단일 문서에서 추출된 규격화 이벤트."""
    date: str = Field(..., description="YYYY-MM-DD 형식의 날짜")
    issue: str = Field(..., description="해당 일자의 핵심 이슈 (1문장)")
    action: str = Field(..., description="실제 수행/조치된 액션 (1문장)")


# ──────────────────────────────────────────────────────────────
# Phase 2: 마이크로 요약
# ──────────────────────────────────────────────────────────────
class PeriodSummary(BaseModel):
    """월별 핵심 동향 요약 (3문장)."""
    summary: str = Field(..., description="해당 월의 핵심 동향 3문장 요약")


class GlobalTheme(BaseModel):
    """전체 프로젝트 관통 테마."""
    theme: str = Field(..., description="전체 프로젝트의 성과/위기 흐름 1문단")


# ──────────────────────────────────────────────────────────────
# Phase 4-B: 백서 기획
# ──────────────────────────────────────────────────────────────
class OutlineItem(BaseModel):
    """백서 목차 항목. target_period는 grouped_chunks 키와 정합해야 함."""
    index: int = Field(..., description="0부터 시작하는 목차 인덱스")
    title: str = Field(..., description="섹션 제목")
    target_period: str = Field(..., description="다룰 데이터의 'YYYY-MM' 키 (단일)")
    intent: str = Field(..., description="이 섹션에서 전달할 핵심 메시지 1문장")


class Outline(BaseModel):
    """전체 목차."""
    items: List[OutlineItem] = Field(..., description="시계열 순서대로 정렬된 목차 리스트")


class OutlineCritique(BaseModel):
    """기획 검수 결과."""
    is_outline_approved: bool = Field(..., description="목차 승인 여부")
    feedback: str = Field(..., description="반려 사유 또는 승인 코멘트")


# ──────────────────────────────────────────────────────────────
# Phase 4-B: 섹션 집필 및 팩트체크
# ──────────────────────────────────────────────────────────────
class SectionDraft(BaseModel):
    """섹션 초안."""
    content: str = Field(..., description="마크다운 형식의 섹션 본문")


class FactCheckResult(BaseModel):
    """팩트체커 응답.

    v1.1: hallucinated_terms 필드를 강제하여
    재작성 시 사용 금지 토큰 블랙리스트로 활용.
    """
    is_draft_approved: bool = Field(..., description="환각/오류 없으면 True")
    feedback: str = Field(..., description="반려 사유 (어떤 부분이 원본에 없는지 명시)")
    hallucinated_terms: List[str] = Field(
        default_factory=list,
        description="원본에 없는데 초안에 등장한 고유명사/날짜/수치 토큰 리스트"
    )


# ──────────────────────────────────────────────────────────────
# 윤문 단계
# ──────────────────────────────────────────────────────────────
class PolishedDocument(BaseModel):
    """윤문된 최종 문서."""
    content: str = Field(..., description="문장 연결만 다듬어진 최종 마크다운 본문")
