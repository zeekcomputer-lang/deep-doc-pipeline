"""
LangGraph global state — v3.0.
"""
from __future__ import annotations
import operator
from typing import TypedDict, List, Dict, Annotated, Any


def update_dict(a: Dict, b: Dict) -> Dict:
    return {**a, **b}


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
    
    # Phase 5: Assembly + Polish + Translate
    final_compiled: str
    english_output: str
    final_output: str
    
    # Phase 6: DOCX
    docx_path: str
