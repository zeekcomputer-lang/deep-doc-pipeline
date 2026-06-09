"""
Pydantic schema definitions for v3.0.
All LLM responses are validated through these schemas via extract_json + model_validate.
"""
from __future__ import annotations
from typing import List
from pydantic import BaseModel, Field


# Phase 1: Extraction (unchanged)
class ExtractedEvent(BaseModel):
    date: str = Field(..., description="Date in YYYY-MM-DD format")
    issue: str = Field(..., description="Key issue on this date (1 sentence)")
    action: str = Field(..., description="Actual action taken (1 sentence)")


# Phase 2: Period Digest (v3 — replaces PeriodSummary)
class PeriodDigest(BaseModel):
    digest: str = Field(..., description="1-2 sentence factual digest of this period")
    event_count: int = Field(..., description="Number of events in this period")
    key_metrics: List[str] = Field(default_factory=list, description="Extracted numeric KPIs")


# Phase 3: Strategic Analysis (v3 — new)
class SectionPlan(BaseModel):
    title: str = Field(..., description="Section title (concise, in English)")
    narrative: str = Field(..., description="Core narrative this section conveys (1-2 sentences)")
    evidence_periods: List[str] = Field(..., description="YYYY-MM periods to draw evidence from")
    key_points: List[str] = Field(..., description="3-5 must-include key points")


class DocumentBlueprint(BaseModel):
    doc_title: str = Field(..., description="Document title (concise, compelling)")
    doc_subtitle: str = Field(..., description="Subtitle with date range or scope")
    section_1: SectionPlan = Field(..., description="Plan for body page 1")
    section_2: SectionPlan = Field(..., description="Plan for body page 2")


# Phase 4: Writing + Fact-check (kept from v2)
class SectionDraft(BaseModel):
    content: str = Field(..., description="Section body in markdown format")


class FactCheckResult(BaseModel):
    is_draft_approved: bool = Field(..., description="True if no hallucinations found")
    feedback: str = Field(..., description="Rejection reason")
    hallucinated_terms: List[str] = Field(default_factory=list, description="Hallucinated tokens")


# Phase 5: Polish + Translate (kept from v2)
class PolishedDocument(BaseModel):
    content: str = Field(..., description="Polished markdown body")
