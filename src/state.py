"""
LangGraph 전역 상태 (v1.1 구조적 위험 보강 반영).
"""
from __future__ import annotations
import operator
from typing import TypedDict, List, Dict, Annotated, Any


def update_dict(a: Dict, b: Dict) -> Dict:
    """Dict reducer — 원본 명세서의 {a, b} 오타 수정본."""
    return {**a, **b}


class GraphState(TypedDict, total=False):
    # [1. 초기 입력]
    target_format: str                              # "status_report" | "whitepaper"
    raw_docs: List[Dict[str, Any]]                  # 원본 JSONL 문서 전체

    # [2. Map-Reduce (추출 및 검증)]
    extracted_events: Annotated[List[Dict], operator.add]

    # [3. 계층적 데이터 압축]
    grouped_chunks: Dict[str, List[Dict]]                       # "YYYY-MM" → events
    period_summaries: Annotated[Dict[str, str], update_dict]    # "YYYY-MM" → summary
    global_theme: str

    # [4. 백서 기획 트랙 루프 제어]
    outline: List[Dict]
    outline_feedback: str
    is_outline_approved: bool
    outline_retry_count: int                        # 무한 루프 방지 (최대 3회)

    # [5. 섹션 집필 및 팩트체크 루프 제어]
    current_section_index: int
    current_draft: str
    previous_draft: str                             # ⭐v1.1: 회귀 방지
    hallucinated_tokens: Annotated[List[str], operator.add]  # ⭐v1.1: 사용 금지 토큰
    draft_feedback: str
    is_draft_approved: bool
    section_retry_count: int                        # Fail-Safe (최대 3회)
    completed_sections: Annotated[Dict[int, str], update_dict]
    unverified_sections: Annotated[List[int], operator.add]   # ⭐v1.1: 감사 로그

    # [6. 최종 산출물]
    final_compiled: str                             # ⭐v1.1: polish 이전 순수 조립본
    final_output: str                               # 최종 (polish + 2차 검증)
    polish_retry_count: int                         # ⭐v1.1: polish 재시도
