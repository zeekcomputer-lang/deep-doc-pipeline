"""LangGraph graph assembly — v3.0."""
from __future__ import annotations
from langgraph.graph import StateGraph, START, END
from .state import GraphState
from . import nodes as N


def build_graph():
    g = StateGraph(GraphState)

    # Phase 1: Extraction
    g.add_node("load_docs", N.load_docs_node)
    g.add_node("strict_extractor", N.strict_extractor_node)
    g.add_node("chrono_sorter", N.chrono_sorter_node)

    g.add_edge(START, "load_docs")
    g.add_conditional_edges("load_docs", N.fanout_to_extractor, ["strict_extractor"])
    g.add_edge("strict_extractor", "chrono_sorter")

    # Phase 2: Compression
    g.add_node("period_digest", N.period_digest_node)
    g.add_conditional_edges("chrono_sorter", N.fanout_to_period_digest, ["period_digest"])

    # Phase 3: Strategic Analysis
    g.add_node("strategic_analyst", N.strategic_analyst_node)
    g.add_edge("period_digest", "strategic_analyst")

    # Phase 4: Writing Loop
    g.add_node("init_writing", N.init_writing_node)
    g.add_node("section_writer", N.section_writer_node)
    g.add_node("fact_checker", N.fact_checker_node)
    g.add_node("retry_section", N.retry_section_node)
    g.add_node("save_section", N.save_section_node)
    g.add_node("save_section_with_warning", N.save_section_with_warning_node)

    g.add_edge("strategic_analyst", "init_writing")
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

    # After save: next section or assembler
    g.add_conditional_edges(
        "save_section",
        N.route_next_section,
        {"section_writer": "section_writer", "assembler": "assembler"},
    )
    g.add_conditional_edges(
        "save_section_with_warning",
        N.route_next_section,
        {"section_writer": "section_writer", "assembler": "assembler"},
    )

    # Phase 5: Assembly + Polish + Translate
    g.add_node("assembler", N.assembler_node)
    g.add_node("polish", N.polish_node)
    g.add_node("translate", N.translate_node)

    g.add_edge("assembler", "polish")
    g.add_edge("polish", "translate")

    # Phase 6: DOCX
    g.add_node("docx_builder", N.docx_builder_node)
    g.add_edge("translate", "docx_builder")
    g.add_edge("docx_builder", END)

    return g.compile()
