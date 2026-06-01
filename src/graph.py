"""
LangGraph 그래프 조립.
"""
from __future__ import annotations
from langgraph.graph import StateGraph, START, END

from .state import GraphState
from . import nodes as N


def build_graph():
    g = StateGraph(GraphState)

    # ── Phase 1: 추출
    g.add_node("load_docs", N.load_docs_node)
    g.add_node("strict_extractor", N.strict_extractor_node)
    g.add_node("chrono_sorter", N.chrono_sorter_node)

    g.add_edge(START, "load_docs")
    g.add_conditional_edges("load_docs", N.fanout_to_extractor, ["strict_extractor"])
    g.add_edge("strict_extractor", "chrono_sorter")

    # ── Phase 2: 요약
    g.add_node("period_summarizer", N.period_summarizer_node)
    g.add_node("theme_analyzer", N.theme_analyzer_node)

    g.add_conditional_edges("chrono_sorter", N.fanout_to_period_summarizer, ["period_summarizer"])
    g.add_edge("period_summarizer", "theme_analyzer")

    # ── Whitepaper-only (v1.3: status_report removed)
    g.add_node("draft_planner", N.draft_planner_node)
    g.add_node("planner_critique", N.planner_critique_node)

    g.add_edge("theme_analyzer", "draft_planner")

    # ── Phase 4-B [1단계]: 기획 루프
    g.add_edge("draft_planner", "planner_critique")

    # ── Phase 4-B [2단계]: 집필 루프
    g.add_node("init_writing", N.init_writing_node)
    g.add_node("section_writer", N.section_writer_node)
    g.add_node("fact_checker", N.fact_checker_node)
    g.add_node("retry_section", N.retry_section_node)
    g.add_node("save_section", N.save_section_node)
    g.add_node("save_section_with_warning", N.save_section_with_warning_node)

    g.add_conditional_edges(
        "planner_critique",
        N.route_outline,
        {"draft_planner": "draft_planner", "init_writing": "init_writing"},
    )
    g.add_edge("init_writing", "section_writer")
    g.add_edge("section_writer", "fact_checker")
    g.add_conditional_edges(
        "fact_checker",
        N.route_section_draft,
        {
            "retry_section": "retry_section",
            "save_section": "save_section",
            "save_section_with_warning": "save_section_with_warning",
        },
    )
    g.add_edge("retry_section", "section_writer")

    # save 이후: 다음 섹션 or compiler
    g.add_conditional_edges(
        "save_section",
        N.route_next_section,
        {"section_writer": "section_writer", "compiler": "compiler"},
    )
    g.add_conditional_edges(
        "save_section_with_warning",
        N.route_next_section,
        {"section_writer": "section_writer", "compiler": "compiler"},
    )

    # ── Phase 4-B [3단계]: 조립 → 윤문 → 2차 검증
    g.add_node("compiler", N.compiler_node)
    g.add_node("polish", N.polish_node)
    g.add_node("final_fact_checker", N.final_fact_checker_node)
    g.add_node("retry_polish", N.retry_polish_node)
    g.add_node("fallback_to_compiled", N.fallback_to_compiled_node)

    g.add_edge("compiler", "polish")
    g.add_edge("polish", "final_fact_checker")
    g.add_conditional_edges(
        "final_fact_checker",
        N.route_final_check,
        {
            "prepare_translation": "prepare_translation",
            "retry_polish": "retry_polish",
            "fallback_to_compiled": "fallback_to_compiled",
        },
    )
    g.add_edge("retry_polish", "polish")
    g.add_edge("fallback_to_compiled", "prepare_translation")

    # ── Phase 5: Translation (English → Korean)
    g.add_node("prepare_translation", N.prepare_translation_node)
    g.add_node("translate", N.translate_node)
    g.add_node("translation_checker", N.translation_checker_node)
    g.add_node("retry_translate", N.retry_translate_node)
    g.add_node("fallback_english", N.fallback_english_node)

    g.add_edge("prepare_translation", "translate")
    g.add_edge("translate", "translation_checker")
    g.add_conditional_edges(
        "translation_checker",
        N.route_translation,
        {
            "END": END,
            "retry_translate": "retry_translate",
            "fallback_english": "fallback_english",
        },
    )
    g.add_edge("retry_translate", "translate")
    g.add_edge("fallback_english", END)

    return g.compile()
