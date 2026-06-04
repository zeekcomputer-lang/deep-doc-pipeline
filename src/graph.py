"""
LangGraph graph assembly.
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

    # ── Phase 3: Planning
    g.add_node("draft_planner", N.draft_planner_node)
    g.add_node("planner_critique", N.planner_critique_node)

    g.add_edge("theme_analyzer", "draft_planner")

    # ── Phase 3 loop: planner → critique
    g.add_edge("draft_planner", "planner_critique")

    # ── Phase 4: Writing loop
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

    # After save: next section or compiler
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

    # ── Phase 4: Assembly → Polish
    g.add_node("compiler", N.compiler_node)
    g.add_node("polish", N.polish_node)

    g.add_edge("compiler", "polish")

    # ── Phase 5: Translation (English → Korean)
    g.add_node("prepare_translation", N.prepare_translation_node)
    g.add_node("translate", N.translate_node)

    g.add_edge("polish", "prepare_translation")
    g.add_edge("prepare_translation", "translate")
    g.add_edge("translate", END)

    return g.compile()


def build_resume_graph(resume_from: str):
    """Resume graph — 특정 Phase부터 재실행.

    resume_from:
        "translate" — Phase 5만 (english_output 필요)
        "polish"    — Phase 4b(polish) + Phase 5 (final_compiled 필요)
        "compile"   — Phase 4b(compile+polish) + Phase 5 (completed_sections + outline 필요)
    """
    g = StateGraph(GraphState)

    if resume_from == "translate":
        g.add_node("prepare_translation", N.prepare_translation_node)
        g.add_node("translate", N.translate_node)
        g.add_edge(START, "prepare_translation")
        g.add_edge("prepare_translation", "translate")
        g.add_edge("translate", END)

    elif resume_from == "polish":
        g.add_node("polish", N.polish_node)
        g.add_node("prepare_translation", N.prepare_translation_node)
        g.add_node("translate", N.translate_node)
        g.add_edge(START, "polish")
        g.add_edge("polish", "prepare_translation")
        g.add_edge("prepare_translation", "translate")
        g.add_edge("translate", END)

    elif resume_from == "compile":
        g.add_node("compiler", N.compiler_node)
        g.add_node("polish", N.polish_node)
        g.add_node("prepare_translation", N.prepare_translation_node)
        g.add_node("translate", N.translate_node)
        g.add_edge(START, "compiler")
        g.add_edge("compiler", "polish")
        g.add_edge("polish", "prepare_translation")
        g.add_edge("prepare_translation", "translate")
        g.add_edge("translate", END)

    else:
        raise ValueError(f"Unknown resume_from: {resume_from!r}. "
                         f"Use: translate / polish / compile")

    return g.compile()
