"""
Pydantic schema definitions.
All LLM responses are validated through these schemas via extract_json + model_validate.

v1.3: All field descriptions in English (LLM English-output enforcement).
      Added TranslationCheckResult for EN→KR translation verification.
"""
from __future__ import annotations
from typing import List
from pydantic import BaseModel, Field


# ──────────────────────────────────────────────────────────────
# Phase 1: Information Extraction
# ──────────────────────────────────────────────────────────────
class ExtractedEvent(BaseModel):
    """Structured event extracted from a single document."""
    date: str = Field(..., description="Date in YYYY-MM-DD format")
    issue: str = Field(..., description="Key issue on this date (1 sentence)")
    action: str = Field(..., description="Actual action taken (1 sentence)")


# ──────────────────────────────────────────────────────────────
# Phase 2: Micro Summaries
# ──────────────────────────────────────────────────────────────
class PeriodSummary(BaseModel):
    """Monthly key trend summary (3 sentences)."""
    summary: str = Field(..., description="3-sentence summary of key trends for this period")


class GlobalTheme(BaseModel):
    """Overall project theme."""
    theme: str = Field(..., description="1-paragraph insight on overall project performance and risk trends")


# ──────────────────────────────────────────────────────────────
# Phase 4-B: Whitepaper Planning
# ──────────────────────────────────────────────────────────────
class OutlineItem(BaseModel):
    """Whitepaper outline item. target_period must match grouped_chunks keys."""
    index: int = Field(..., description="0-based outline index")
    title: str = Field(..., description="Section title")
    target_period: str = Field(..., description="'YYYY-MM' key for the data period (single)")
    intent: str = Field(..., description="Key message for this section (1 sentence)")


class Outline(BaseModel):
    """Complete outline."""
    items: List[OutlineItem] = Field(..., description="Outline items in chronological order")


class OutlineCritique(BaseModel):
    """Planning review result."""
    is_outline_approved: bool = Field(..., description="Whether the outline is approved")
    feedback: str = Field(..., description="Rejection reason or approval comment")


# ──────────────────────────────────────────────────────────────
# Phase 4-B: Section Writing and Fact-checking
# ──────────────────────────────────────────────────────────────
class SectionDraft(BaseModel):
    """Section draft."""
    content: str = Field(..., description="Section body in markdown format")


class FactCheckResult(BaseModel):
    """Fact-checker response.

    v1.1: hallucinated_terms field mandatory — used as
    banned-token blacklist during rewrites.
    """
    is_draft_approved: bool = Field(..., description="True if no hallucinations or errors found")
    feedback: str = Field(..., description="Rejection reason (specify which parts are not in the source)")
    hallucinated_terms: List[str] = Field(
        default_factory=list,
        description="Tokens (proper nouns/dates/numbers) in the draft but not in the source"
    )


# ──────────────────────────────────────────────────────────────
# Polish Stage
# ──────────────────────────────────────────────────────────────
class PolishedDocument(BaseModel):
    """Polished final document."""
    content: str = Field(..., description="Final markdown body with only sentence flow refined")


# ──────────────────────────────────────────────────────────────
# Translation Stage (English → Korean)
# ──────────────────────────────────────────────────────────────
class TranslationCheckResult(BaseModel):
    """Translation verification result for EN→KR stage."""
    is_approved: bool = Field(
        ..., description="True if translation faithfully preserves all facts and proper nouns"
    )
    missing_terms: List[str] = Field(
        default_factory=list,
        description="Proper nouns/terms present in English but missing or altered in Korean"
    )
    added_terms: List[str] = Field(
        default_factory=list,
        description="Terms in Korean translation not found in English original"
    )
    feedback: str = Field(..., description="Specific issues found, or approval comment")
